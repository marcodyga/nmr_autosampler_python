import time
import os
import subprocess
import threading
import traceback
import copy
import logging
from AcdMacro import AcdMacro

class Queue:
    """
    The Queue class handles managing the queue. It reads the queue from the database, runs measurements, and sets the results in the database.
    It controls both the Autosampler and the Spectrometer.
    """
    
    def __init__(self, autosampler, spinsolve, mysql_reader):
        """
        Create a Queue object.
        Requires an existing Autosampler and Spinsolve object, which have to be passed to this
        constructor method as arguments.
        
        Arguments:
        autosampler  -- the Autosampler object which should be used for the queue
        spinsolve    -- the Spinsolve object
        mysql_user   -- the mysql username for the queue db
        mysql_passwd -- mysql password
        mysql_host   -- hostname of the mysql server
        mysql_db     -- name of the autosampler database.
        """
        self.autosampler = autosampler
        self.spinsolve = spinsolve
        self.acd_macro_running = False
        
        # connect to mysql database
        self.mysql_reader = mysql_reader
        conn, cur = self.mysql_reader.connect_db()
        
        # set queue and shimming to not running, when the queue is initialized.
        cur.execute("UPDATE QueueAbort SET QueueStat = 0")
        cur.execute("UPDATE shimming SET Shimming = 0")
        
        # start queue daemon
        self.qd = threading.Thread(target=self.queue_daemon, args=())
        self.qd.daemon = True
        self.qd.start()
        
        # start progress daemon
        self.pd = threading.Thread(target=self.progress_daemon, args=())
        self.pd.daemon = True
        self.pd.start()
        
        conn.close()
    
    def connect_db(self):
        """
        For reasons unknown, only one cursor per connection works properly
        After using the DB, make sure to close the connection with conn.close
        """
        return self.mysql_reader.connect_db()
    
    def read_db(self):
        """
        Reads data from autosampler db.
        
        returns :
        samples -- sample table in nested tuple / dictionary format
        shimming -- shimming table (one row) as dictionary
        queueabort -- queueabort table, a.k.a. the abort flag as dictionary
        
        short reference on the meaning of these flags:
        shimming['Shimming']:  0 = shimming ok
                               1 = someone gave the order for shimming, we are performing checkshim now
                               2 = we are performing quickshim now
                               3 and 4 = the other quickshims failed and we are performing more quickshims
                               5 = 3x quickshim failed, we are aborting now
        queueabort['QueueStat']: 0 = queue is either not running, or was aborted.
                                 1 = someone gave the order to start the queue.
                                 
                               
        
        """
        conn, cur = self.connect_db()
        if conn == None or cur == None:
            return None, None, None
        samples = self.mysql_reader.read_samples(conn, cur)
        shimming = self.mysql_reader.read_shimming(conn, cur)
        queueabort = self.mysql_reader.read_queueabort(conn, cur)
        conn.close()
        return samples, shimming, queueabort
    
    def queue_daemon(self):
        """
        Queue daemon always runs in the background and continually reads the database.
        Any event should be handled through this daemon, which is supposed to spring into action
        as soon as it detects changes in the database.
        The daemon is blocking, which means that, during a measurement, it will wait for the 
        functions it calls to finish.
        Only one queue daemon is allowed to run at the same time, otherwise it could be possible 
        that multiphle daemons conflict with each other.
        """
        first_sample = True  # only True if the current sample is the first one of the queue
                             # (in this case: this is only allowed to reset when there has been
                             # a phase of not measuring any samples)
        previous_sample = 32  # if first_sample is false, then this is used to determine if the 
                              # sample must be inserted again or if it is still inside
        same_sample = False # if the same sample should be measured multiple times, this 
                            # flag will be set to True. Then it knows that it can skip 
                            # inserting
        while True:
            try:
                # check if connected to autosampler, spectrometer, and if autosampler is ready
                # if the same sample is measured multiple times the errorcode will be 3 instead of 0
                if self.autosampler.ser != False and self.spinsolve.socket != False and (self.autosampler.errorcode == 0 or (same_sample and self.autosampler.errorcode == 3)):
                    # read database
                    samples, shimming, queueabort = self.read_db()
                    # start measuring samples.
                    SinceShim = time.time() - shimming['LastShim']
                    # queuestat is 1 --> we are going to run the queue.
                    if queueabort['QueueStat'] == 1:
                        conn, cur = self.connect_db()
                        if conn is not None and cur is not None:
                            # if possible, there should be no Running samples, but if there are, they are probably due to a prior crash, so lets restart them.
                            # we get a list of all Running samples sorted by ID...
                            cur.execute("SELECT * FROM samples WHERE Status = 'Running' ORDER BY ID ASC")
                            running_samples = cur.fetchall()
                            if running_samples:
                                sample = running_samples[0]
                            else:
                                # this is the normal case where no Running samples were found.
                                # we get a list of all Queued samples sorted by ID, and set "sample" as the next queued sample.
                                cur.execute("SELECT * FROM samples WHERE Status = 'Queued' ORDER BY ID ASC")
                                queued_samples = cur.fetchall()
                                if queued_samples:
                                    sample = queued_samples[0]
                                else:
                                    sample = None
                            # now we measure the first one of those
                            # since we are running a daemon, the next one will be automatically measured later
                            # on, as long as QueueStat stays at 1.
                            if sample == None:
                                # if no sample is queued, the QueueStat will be reset to 0, which will unhide
                                # the buttons in the Table.
                                cur.execute("UPDATE QueueAbort SET QueueStat = 0")
                            else:
                                cur.execute("UPDATE samples SET Status = 'Running' WHERE ID = " + str(sample['ID']))
                                logging.info("Measuring sample " + sample['Name'] + " with ID = " + str(sample['ID']) + ".")
                                as_status = int(self.autosampler.errorcode)
                                # check if the page can connect to autosampler. if yes, use it; if not, just start measuring
                                inserted = False
                                if not first_sample and previous_sample == sample['Holder'] and same_sample:
                                    # check if the sample was measured before and is still in the spectrometer
                                    # if this is the case, no need to re-insert it
                                    inserted = True
                                    # reset the same_sample flag here, now it's "used" and the correct sample is in. the program
                                    # can run as if a new sample was actually inserted now.
                                    same_sample = False
                                else:
                                    inserted = self.autosampler.insert_sample(sample['Holder'], not first_sample)
                                # ok if insertion was successful, we can start measuring.
                                if inserted:
                                    # find out what type of measurement it is
                                    if sample['SampleType'] == "CheckShim" or sample['SampleType'] == "QuickShim" or sample['SampleType'] == "PowerShim":
                                        # Shimming sample
                                        logging.info("Begin shimming of type " + sample['SampleType'] + ".")
                                        cur.execute("UPDATE shimming SET Shimming = 1")
                                        success, aborted = self.spinsolve.shim(sample['SampleType'])
                                        if not aborted:
                                            if sample['SampleType'] == "CheckShim":
                                                # Shim as required: Checkshim, then up to 3x Quickshim.
                                                if success:
                                                    t = int(time.time())
                                                    cur.execute("UPDATE shimming SET Shimming = 0, LastShim = " + str(t))
                                                    logging.info("CheckShim successful.")
                                                    shimming['Shimming'] = 0
                                                    shimming['LastShim'] = t
                                                else:
                                                    # if checkshim failed, we need to do quickshims now.
                                                    cur.execute("UPDATE shimming SET Shimming = 2")
                                                    shimming['Shimming'] = 2
                                                    while shimming['Shimming'] >= 2 and shimming['Shimming'] < 5:
                                                        # if one quickshim fails, do up to 2 more quickshims before giving up.
                                                        logging.info("Performing QuickShim...")
                                                        success, aborted = self.spinsolve.shim('QuickShim')
                                                        if aborted:
                                                            break
                                                        if success:
                                                            t = int(time.time())
                                                            cur.execute("UPDATE shimming SET Shimming = 0, LastShim = " + str(t))
                                                            logging.info("QuickShim successful.")
                                                            shimming['Shimming'] = 0
                                                            shimming['LastShim'] = t
                                                        else:
                                                            shimming['Shimming'] += 1
                                                            cur.execute("UPDATE shimming SET Shimming = " + str(shimming['Shimming']))
                                                            if shimming['Shimming'] > 4:
                                                                logging.info("QuickShim failed three times. Check if shimming sample (10% D<sub>2</sub>O + 90% H<sub>2</sub>O) is inserted correctly and try again.")
                                                                cur.execute("UPDATE shimming SET Shimming = 0, LastShim = 0")
                                            elif sample['SampleType'] == "QuickShim" or sample['SampleType'] == "PowerShim":
                                                cur.execute("UPDATE shimming SET Shimming = 0")
                                                if success:
                                                    t = int(time.time())
                                                    cur.execute("UPDATE shimming SET LastShim = " + str(t))
                                                    logging.info(sample['SampleType'] + " successful.")
                                                    shimming['Shimming'] = 0
                                                    shimming['LastShim'] = t
                                    else:
                                        # Real sample
                                        # 1D PROTON+ is called 1D EXTENDED+ in Spinsolve software.
                                        if sample['Protocol'] == "1D PROTON+":
                                            sample['Protocol'] = "1D EXTENDED+"
                                        # filter the different options depending on the protocol
                                        # let's keep the code simple for now, just do a manual implementation of
                                        # proton and fluorine NMR.
                                        options = {}
                                        if sample['Protocol'] == "1D EXTENDED+":
                                            options['PulseAngle'] = "90"
                                            options['Number'] = sample['Number']
                                            options['RepetitionTime'] = sample['RepTime']
                                            acquisition_times = [0.4, 0.8, 1.6, 3.2, 6.4]
                                        if sample['Protocol'] == "1D FLUORINE+":
                                            options['PulseAngle'] = "90"
                                            options['Number'] = sample['Number']
                                            options['RepetitionTime'] = sample['RepTime']
                                            acquisition_times = [0.32, 0.64, 1.64, 3.2]
                                        # in Magritek's software, repetition time is just acquisition time which is not recorded in the fid.
                                        # always use the highest value possible for acquisition time.
                                        for acquisition_time in sorted(acquisition_times, reverse=True):
                                            if acquisition_time < sample['RepTime']:
                                                break
                                        options['AcquisitionTime'] = acquisition_time
                                        success, aborted = self.spinsolve.measure_sample(sample['Name'], sample['Protocol'], options, sample['Solvent'])
                                    if aborted:
                                        # Sample aborted
                                        cur.execute("UPDATE samples SET Status = 'Failed' WHERE ID = " + str(sample['ID']))
                                        cur.execute("UPDATE shimming SET Shimming = 0")
                                        logging.info("Measurement aborted.")
                                    elif success:
                                        # successfully measured
                                        logging.debug("Measurement done.")
                                        cur.execute("UPDATE samples SET Status = 'Finished', Progress = 100 WHERE ID = " + str(sample['ID']))
                                        # start the automatic evaluation using ACD specman
                                        MacroSuccess = False
                                        while self.acd_macro_running == True:
                                            # wait for the last macro to finish before running the next one.
                                            time.sleep(0.5)
                                        try:
                                            MacroSuccess = self.fnmr_macro(sample['Name'], sample['Method'])
                                        except:
                                            logging.error("Error while evaluating sample " + sample["Name"] + ".")
                                            self.acd_macro_running = False
                                        logging.info("Sample " + sample['Name'] + " was measured successfully.")
                                    else:
                                        # error when measuring sample
                                        cur.execute("UPDATE samples SET Status = 'Failed' WHERE ID = " + str(sample['ID']))
                                        logging.info("Error when measuring the sample.")
                                else:
                                    # raise an error to the Autosampler, if it doesn't know the error itself
                                    timeout = 5 # seconds
                                    while timeout > 0:
                                        timeout = timeout - 1
                                        if self.autosampler.is_error():
                                            # error has been caught
                                            break
                                        elif timeout <= 0:
                                            # error has not been caught after timeout
                                            self.autosampler.raise_error()
                                            logging.warning("Raising error to Autosampler: Failed to insert sample while errorcode is not known!")
                                        time.sleep(1)
                                    # Special case: errorcode 6 (sample was detected in spectrometer). In this case, do not set status to failed, but interrupt the queue.
                                    if self.autosampler.errorcode == 6:
                                        cur.execute("UPDATE samples SET Status = 'Queued' WHERE ID = " + str(sample['ID']))
                                    # interrupt the queue if an error in the autosampler occured (inserted != True)
                                    else: 
                                        cur.execute("UPDATE samples SET Status = 'Failed' WHERE ID = " + str(sample['ID']))
                                # return the sample to the holder in the Autosampler.
                                logging.info("Returning sample.")
                                as_status = int(self.autosampler.errorcode)
                                returned = False
                                if as_status == 3:
                                    # Check for any modifications which may have occured during the measurement
                                    cur.execute("SELECT * FROM samples WHERE Status = 'Queued' ORDER BY ID ASC")
                                    queued_samples = cur.fetchall()
                                    if len(queued_samples) >= 1 and queued_samples[0]['Holder'] == sample['Holder']:
                                        # next sample is the same holder as the current one, so don't remove
                                        # it from the spectrometer
                                        returned = True
                                        # set the same_sample flag
                                        same_sample = True
                                    else:
                                        returned = self.autosampler.return_sample(sample['Holder'])
                                    first_sample = False
                                    last_sample = sample['Holder']
                                # if the sample was not returned from the autosampler, halt the queue and raise
                                # an error to the autosampler, if it doesn't know the error itself.
                                if not returned:
                                    timeout = 5 # seconds
                                    while timeout > 0:
                                        timeout = timeout - 1
                                        if self.autosampler.is_error():
                                            # error has been caught
                                            break
                                        elif timeout <= 0:
                                            # error has not been caught after timeout
                                            self.autosampler.raise_error()
                                            logging.warning("Raising error to Autosampler: Failed to return sample while errorcode is not known!")
                                        time.sleep(1)
                                    logging.info("Sample " + sample['Name'] + "could not be returned.")
                            conn.close()
                    
                    # if both queue and shimming are not running, the first_sample flag will be reset.
                    if queueabort['QueueStat'] == 0 and shimming['Shimming'] == 0:
                        first_sample = True
                    
                    # if the autosampler encounters an error, we want to cancel the queue.
                    if self.autosampler.is_error():
                        conn, cur = self.connect_db()
                        if conn is not None and cur is not None:
                            cur.execute("UPDATE QueueAbort SET QueueStat = 0")
                            conn.close()
            except Exception as e:
                logging.error("OMG Something TERRIBLE happened to the queue daemon!!!!! :-(")
                traceback.print_exc()
            time.sleep(1)

    
    def fnmr_macro(self, fname, method):
        """
        Automatically evaluate NMR spectrum using ACD NMR Processor.
        
        Arguments:
        fname    -- Folder name of the NMR spectrum
        method   -- The method ID in the mysql DB
        """
        macro = AcdMacro(self.mysql_reader, fname, method)
        macro.run_macro()
        # wait till macro thread is running
        for i in range(10):
            time.sleep(0.1)
            if macro.running == True:
                self.acd_macro_running = True
                break
        # run a "resetter" as a thread to reset self.acd_macro_running.
        def fnmr_macro_resetter(self, macro):
            while macro.running == True:
                time.sleep(0.5)
            self.acd_macro_running = False
        macro_resetter = threading.Thread(target=fnmr_macro_resetter, args=(self, macro))
        macro_resetter.daemon = True
        macro_resetter.start()
        
    
    def progress_daemon(self):
        """
        Reads the progress from the NMR, and writes it into the database.
        """
        last_progress = 0
        progress = 0
        while True:
            try:
                if self.spinsolve.progress:
                    last_progress = copy.deepcopy(progress)
                    progress = self.spinsolve.progress
                    samples, shimming, queueabort = self.read_db()
                    if samples is not None:
                        if last_progress != progress:
                            conn, cur = self.connect_db()
                            if shimming['Shimming'] > 0:
                                cur.execute("UPDATE shimming SET ShimProgress = " + str(progress))
                            if queueabort['QueueStat'] == 1:
                                cur.execute("SELECT * FROM samples WHERE Status = 'Running' ORDER BY ID ASC")
                                samples = cur.fetchall()
                                if samples:
                                    sample = samples[0]
                                    cur.execute("UPDATE samples SET Progress = " + str(progress) + " WHERE ID = " + str(sample['ID']))
                            conn.close()
            except:
                logging.error("Something TERRIBLE happened to the progress daemon!!! :-(")
                traceback.print_exc()
            time.sleep(1)
    
    def start_queue(self):
        """
        starts the queue for debug purposes.
        """
        conn, cur = self.connect_db()
        if conn is not None and cur is not None:
            cur.execute("UPDATE QueueAbort SET QueueStat = 1")
            conn.close()
        