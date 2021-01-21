import serial
import time
import threading
import logging
from MySQLReader import *

class Autosampler:
    """
    Python Autosampler Control Class
    """

    def __init__(self, mysql_reader):
        """
        Create an Autosampler object which will be able to communicate with the autosampler.
        
        Arguments:
        mysql_reader -- a MySQLReader object with access to the config
        """
        self.mysql_reader = mysql_reader
        config = None
        while config is None:
            config = self.mysql_reader.read_config()
            logging.warning("Can't connect to MySQL server. Waiting for 2 seconds.")
            time.sleep(2)
        
        self.port = config['ASPort']
        self.ser = False       # serial port
        self.errorcode = -1    # error code
        self.last_contact = 0  # timestamp of last contact

        # define all error codes
        self.errorcodelist = {
            -2: "Connection to Autosampler lost!",
            -1: "Could not connect to Autosampler.",
            0: "Autosampler is ready to use.",
            1: "Autosampler is at work!",
            2: "Error in the Autosampler! Please check if a NMR tube is stuck inside.",
            3: "Autosampler reports measurement is running!",
            4: "Error: Pusher did not properly open, please check if a NMR tube is stuck the device!",
            5: "Error: Pusher did not properly close, please check if a NMR tube is stuck the device!",
            6: "Error: A NMR tube was detected inside of the spectrometer before starting the queue. Please remove it from Holder 32.",
            7: "Error: When trying to return a sample, no sample could be detected in the spectrometer!",
            8: "Error: When trying to start a measurement, no sample could be detected in the specified holder!",
            9: "Autosampler reports an unknown error raised by the PC!"
        }
        
        # run the reading part of the communcation script indefinitely
        self.listener = threading.Thread(target=self.listen, args=())
        self.listener.daemon = True
        self.listener.start()
        
    def connect(self):
        """
        Establish connection to the Autosampler.
        
        Returns True if connection was successful.
        """
        try:
            self.ser = serial.Serial(port=self.port, baudrate=9600, parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE, bytesize=serial.EIGHTBITS, xonxoff=False, rtscts=False, dsrdtr=False)
            # ignore the first few bytes of the stuff that is returned from the port, because sometimes
            # the program will complain about "invalid start bytes"
            if self.ser.is_open:
                for i in range(5):
                    buffer_string = self.ser.read(self.ser.inWaiting())
                return True
            else:
                return False
        except:
            logging.error("Failed to connect to autosampler!")
            return False
    
    def disconnect(self):
        """
        Disconnect the Autosampler, and stop listening to the serial port.
        """
        if self.is_connected():
            self.ser.close()
        self.ser = False
            
    def is_connected(self):
        """
        Checks if Autosampler is currently connected.
        """
        if self.ser and self.ser.is_open:
            return True
        else:
            return False

    def listen(self):
        """
        Keeps listening to the Autosampler, if it is connected.
        Should always run in the background as a daemon.
        """
        timeout = 10000
        while True:
            if self.ser != False and self.ser.is_open:
                buffer_string = self.ser.read(self.ser.inWaiting())
                buffer_string = buffer_string.decode('UTF-8')
                if buffer_string != "":
                    self.last_contact = time.time()
                    timeout = 25
                    new_errorcode = int(buffer_string[-1])
                    if new_errorcode != self.errorcode:
                        logging.info("Autosampler status changed from " + str(self.errorcode) + " [" + self.errorcodelist[int(self.errorcode)] + "] to " + str(new_errorcode) + " [" + self.errorcodelist[int(new_errorcode)] + "]!")
                        self.errorcode = int(new_errorcode)
                else:
                    # in this case no comms were received from the Autosampler.
                    # after a certain time of no comms, change the status to -2 (connection lost)
                    # exception is if status is 1, because then it may happen that the autosampler 
                    # doesn't respond for a certain amount of time.
                    if self.errorcode != 1:
                        timeout -= 1
                    if timeout <= 0:
                        self.errorcode = -2
            else:
                self.errorcode = -2
            
            # write to db, so that the webpage can read it.
            conn, cur = self.mysql_reader.connect_db()
            if conn is not None and cur is not None:
                cur.execute("UPDATE as_status SET as_status = " + str(self.errorcode) + ", last_contact = " + str(int(time.time())))
                conn.close()
            
            time.sleep(0.2)
    
    def yell(self, stuff):
        """
        Sends some data to the Autosampler.
        
        Arguments:
        stuff -- the string you want to tell the Autosampler (unicode string).
        """
        if self.is_connected():
            stuff_b = stuff.encode('UTF-8')
            self.ser.write(stuff_b)
        else:
            logging.error("Autosampler is not connected.")
    
    def is_error(self):
        """
        Returns True if Errorcode is 2 or greater than 3, and False otherwise.
        """
        if self.errorcode > 3 or self.errorcode == 2:
            return True
        else:
            return False
    
    def raise_error(self):
        """
        Tells the Autosampler that something TERRIBLE has happened !!!
        This will cause the Autosampler to gain errorcode 9, which will stop it from doing
        anything without user input.
        """
        self.yell("E")
    
    def insert_sample(self, sample, in_queue=False, timeout=120):
        """
        Tells the Autosampler that it should insert a sample.
        
        Arguments:
        sample -- the holder number of the sample which should be measured.
        in_queue -- if this is True, then the Autosampler will not perform a homing and will not 
                    try to eject a sample, which may be stuck in the spectrometer, to Holder 32.
                    This should be set to True, when measure_sample is called within a queue, and
                    a sample has previously been ejected successfully using return_sample.
        timeout -- time in seconds which the program waits until it decides that the insertion
                   was a failure.
        
        Returns True if the sample wsa inserted successfully, and False otherwise.
        """
        inserted = False
        if in_queue:
            self.yell("N" + str(sample))
        else:
            self.yell("M" + str(sample))
        while timeout > 0:
            time.sleep(1)
            timeout -= 1
            if self.errorcode == 3:
                inserted = True
                break
            elif self.is_error():
                inserted = False
                break
        return inserted
    
    def return_sample(self, sample, timeout=120):
        """
        Tells the Autosampler to return the Sample, which is currently in the spectrometer, to
        the holder number specified in sample.
        
        Arguments:
        sample -- the holder number of the sample which should be measured.
        timeout -- time in seconds which the program waits until it decides that the insertion
                   was a failure.
        
        Returns True if the sample wsa returned successfully, and False otherwise.
        """
        returned = False
        self.yell("R" + str(sample))
        while timeout > 0:
            time.sleep(1)
            timeout -= 1
            if self.errorcode == 0:
                returned = True
                break
        return returned
    
    def homing(self):
        """
        Tells the Autosampler to perform a homing to calibrate its rotational position.
        """
        self.yell("h")
    
    def move_to_pos(self, pos):
        """
        Tells the Autosampler to move to a certain holder.
        
        Arguments:
        pos -- the holder number the Autosampler should move to.
        """
        self.yell("m" + str(pos))
