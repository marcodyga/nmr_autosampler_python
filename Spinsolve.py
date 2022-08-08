import socket
import time
import os
import threading
import xml.etree.ElementTree as ET
import traceback
import logging
from datetime import datetime
from MySQLReader import *

class Spinsolve:
    """
    Python Magritek Spinsolve control class
    """
    
    def __init__(self, mysql_reader):
        """
        Create a Spinsolve object which will handle the communication between the spectrometer 
        and the python program.
        
        Arguments:
        mysql_reader -- a MySQLReader object with access to the config
        """
        self.mysql_reader = mysql_reader
        config = self.mysql_reader.read_config()
        
        self.NMRFolder = config['NMRFolder']
        if self.NMRFolder[-1] != "/" and self.NMRFolder[-1] != "\\":
            self.NMRFolder += "/"
        self.socket = False
        self.protocols = False
        self.options = False
        self.last_status = 0
        self.progress = 0
        self.seconds_remaining = 0
        
        self.mysql_reader = mysql_reader
        
        # flags for measurements. will be reset in the measurement functions.
        self.completed = False
        self.completed2 = False
        self.successful = False
        
        # start listener daemon which reads the status of the NMR spectrometer.
        self.listener = threading.Thread(target=self.listen, args=())
        self.listener.daemon = True
        self.listener.start()
    
    def connect(self):
        """
        Connect with the Spinsolve spectrometer.
        Return True if successful, False otherwise.
        """
        config = self.mysql_reader.read_config()
        nmr_ip = config['NMRIP']
        port = config['NMRPort']
        
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.socket.connect((nmr_ip, port))
            return True
        except:
            logging.error("Failed to connect to Spinsolve!")
            return False
    
    def disconnect(self):
        """
        Close the connection to the Spinsolve NMR.
        """
        try:
            self.socket.close()
        except:
            pass
        self.socket = False
        
    def is_connected(self):
        """
        check if socket is connected or not
        """
        if self.socket:
            return True
        else:
            return False
     
    def listen(self):
        """
        Background process which reads the status of the NMR and reports it to the 
        responsible attributes of the class.
        Should always run in the background.
        """
        def process_status_notification(root):
            # Process a status notification
            SN = root.find("StatusNotification")
            if SN != None:
                # refresh the progress attribute, if available
                Progress = SN.find("Progress")
                if Progress != None:
                    self.progress = Progress.get("percentage")
                    self.seconds_remaining = int(Progress.get("secondsRemaining"))
                # set a flag that the measurement was completed/successful. this will be reset by 
                # the function which is waiting for this flag to be set.
                if SN.find("Completed") != None:
                    self.completed = True
                    logging.debug("YEAH! A measurement has just been completed! :)")
                    if SN.find("Completed").get("completed") == "true":
                        # in the case of an aborted shim, completed will be True, but completed2
                        # will be False.
                        self.completed2 = True
                        logging.debug(" -- 'Completed' is True")
                    if SN.find("Completed").get("successful") == "true":
                        self.successful = True
                        logging.debug(" -- 'Successful' is True")
                        
        while True:
            if self.socket != False:
                try:
                    message = self.socket.recv(4096)
                    message = message.decode("UTF-8")
                    # for debugging purposes, log all messages coming form the socket
                    #with open("socket_recv.log", "a") as f:
                    #    print(str(datetime.now()) + " > ", file=f)
                    #    print(str(message) + "\n", file=f)
                    try:
                        root = ET.fromstring(message)
                        #logging.debug(message)
                        process_status_notification(root)
                    except ET.ParseError:
                        # meh some bullshit happend in the xml which was received from the spectrometer
                        # this happens sometimes when multiple xml messages are inside of the message
                        messages = message.split("<?xml ")
                        messages.pop(0) # remove first string which is empty
                        for message in messages:
                            message = "<?xml " + message
                            try:
                                root = ET.fromstring(message)
                                process_status_notification(root)
                            except:
                                # now this is complete bullshit, no idea what happened here, just print 
                                # the goddamn error
                                logging.error("Problem occured with the following message: " + str(message))
                                traceback.print_exc()
                    # Write down the date of the contact with the spectrometer.
                    self.last_status = int(time.time())
                except:
                    logging.warning("It appears that the Spinsolve software is not running, has crashed or was closed by the user. Aborting...")
                    logging.exception("")
                    self.disconnect()
                    conn, cur = self.mysql_reader.connect_db()
                    if conn is not None and cur is not None:
                        cur.execute("UPDATE QueueAbort SET QueueStat = 0")
                        conn.close()
            time.sleep(0.2)
    
    def shim(self, shimtype):
        """
        Perform a shim.
        Argument:
            shimtype --- can be a string, either "CheckShim", "QuickShim" or "PowerShim", which 
                         specifies the shimming which should be performed.
        Returns two booleans:
            retval -- True if the measurement was successful, and False otherwise.
            aborted -- True if the measurement was aborted by user, False otherwise.
        """
        t = time.localtime()
        tstr = time.strftime("%Y-%m-%d_%H%M%S", t)
        retval = False
        aborted = False
        message  = self.message_set("<Sample>Shim" + tstr + "</Sample>")
        message += self.message_set("<DataFolder><UserFolder>" + self.NMRFolder + "Shim" + tstr + "</UserFolder></DataFolder>", False)
        message += ("<Message>"
                      "<Start protocol='SHIM'>"
                        "<Option name='Shim' value='" + shimtype + "' />"
                      "</Start>"
                    "</Message>")
        self.socket.send(message.encode())
        # check if successful
        timeouts = {"CheckShim": 1, "QuickShim": 6, "PowerShim": 60} # in minutes
        timeout = timeouts[shimtype]*10*60 # in 100 ms
        i = 0
        while i < timeout:
            i += 1
            timeout = i + (self.seconds_remaining + 60) * 10
            # did anyone press the abort button?
            queueabort = self.mysql_reader.read_queueabort()
            if queueabort['QueueStat'] == 0 and not aborted:
                self.abort()
                i = timeout - 10 # Give it one second to abort.
                aborted = True
            if self.completed:
                self.completed = False
                if self.successful:
                    self.successful = False
                    SuccessFile = self.NMRFolder + "Shim" + tstr + "/protocol.par"
                    if os.path.isfile(SuccessFile):
                        retval = True
                break
            time.sleep(0.1)
        self.progress = 0    # reset progress
        self.seconds_remaining = 0
        return retval, aborted
    
    def measure_sample(self, name, protocol, options, solvent="None", comment=""):
        """
        Tells the Spectrometer to measure the currently inserted Sample. 
        Writes the spectrum into the folder specified as self.NMRFolder.
        
        Arguments:
            name     -- name of the sample
            protocol -- the protocol's xmlKey ("1D EXTENDED+", "1D FLUORINE+", etc)
            options  -- a dictionary containing the options for the measurement (which options 
                        need to be given depends on the protocol)
            solvent  -- the solvent
            comment  -- a comment
        
        Returns two booleans:
            retval -- True if the measurement was successful, and False otherwise.
            aborted -- True if the measurement was aborted by user, False otherwise.
        """
        retval = False
        aborted = False
        self.progress = 0
        self.seconds_remaining = 0
        # add the general stuff, which is needed for every protocol.
        # Sample name
        message  = self.message_set("<Sample>" + name + "</Sample>")
        # Solvent
        message += self.message_set("<Solvent>" + solvent + "</Solvent>", False)
        # Comment
        message += self.message_set("<UserData><Data key='Comment' value='" + comment + "'/></UserData>", False)
        # Folder
        message += self.message_set("<DataFolder><UserFolder>" + self.NMRFolder + name + "</UserFolder></DataFolder>", False)
        # send all the options in raw format.
        message += "<Message>"
        message +=   "<Start protocol='" + protocol + "'>"
        for option in options:
            message += "<Option name='" + option + "' value='" + str(options[option]) + "'/>"
        message +=   "</Start>"
        message += "</Message>"
        self.socket.send(message.encode())
        # now wait for the measurement to finish...
        timeout = 60*48 # in minutes
        timeout = timeout*10*60 # in 100 ms
        i = 0
        while i < timeout:
            i += 1
            timeout = i + (self.seconds_remaining + 60) * 10
            # did anyone press the abort button?
            queueabort = self.mysql_reader.read_queueabort()
            if queueabort['QueueStat'] == 0 and not aborted:
                logging.info("Detected abort signal!")
                self.abort()
                i = timeout - 10 # Give it one second to abort.
                aborted = True
            if self.completed:
                self.completed = False
                # sometimes there is a delay on slow computers here, need timeout here
                for j in range(10): # 10x100 ms = 1 sec
                    if self.successful == True:
                        break
                    time.sleep(0.1)
                if self.successful:
                    self.successful = False
                    # wait for a few seconds, sometimes spinsolve is kind of slow when generating the files.
                    SuccessFile = self.NMRFolder + name + "/spectrum.1d"
                    for j in range(10): # 10 seconds maximum
                        if os.path.isfile(SuccessFile):
                            retval = True
                            break
                        time.sleep(1)
                break
            time.sleep(0.1)
        else:
            logging.error(f"Measurement failed due to timeout. i={i}, timeout={timeout}")
        self.progress = 0    # reset progress
        self.seconds_remaining = 0
        return retval, aborted
    
    def abort(self):
        """
        Tries to abort the currently running measurement.
        When a measurement is aborted, the StatusNotification for "completed" will fire, which will
        automatically kick out any running loop.
        """
        try:
            message = ("<?xml version='1.0' encoding='UTF-8'?>"
                       "<Message>"
                         "<Abort />"
                       "</Message>")
            self.socket.send(message.encode())
        except:
            pass
    
    def message_set(self, message, doctype=True):
        """
        Helper function which generates the following XML code (used many times to send stuff to
        the spectrometer):
        
        <?xml version='1.0' encoding='UTF-8'?>
        <Message>
          <Set>
            ---- adds here the string from the argument "message"
          </Set>
        </Message>
        
        If the argument "doctype" is set to False, the function will leave out the doctype string 
        (the first line in the example above)
        """
        xml_code = ""
        if doctype:
            xml_code += "<?xml version='1.0' encoding='UTF-8'?>"
        xml_code += ("<Message>"
                       "<Set>"
                         + str(message) +
                       "</Set>"
                     "</Message>")
        return xml_code
