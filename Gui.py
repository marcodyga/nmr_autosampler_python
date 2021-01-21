from datetime import datetime
import ctypes
import logging
import subprocess
import time
import threading
import sys
from PyQt5 import QtWidgets, QtWebEngineWidgets, QtGui, QtCore, uic
from PyQt5.Qt import QUrl

class Gui:
    """
    The GUI component.
    """
    
    def __init__(self):
        self.app = QtWidgets.QApplication(sys.argv)
        # display loading screen
        self.splash = QtWidgets.QSplashScreen(QtGui.QPixmap('loading.png'))
        self.splash.show()
    
    def initialize(self, queue, xampp_location):
        self.autosampler = queue.autosampler
        self.spinsolve = queue.spinsolve
        self.mysql_reader = queue.mysql_reader
        self.queue = queue
        self.heavy_counter = 9999
        self.xampp_location = xampp_location
    
        self.window = uic.loadUi("autosampler.ui")
        self.browser = None
        
        self.setup()
        
        # start a daemon for polling data and refreshing the content of the window
        self.gui_daemon = threading.Thread(target=self.loop, args=())
        self.gui_daemon.daemon = True
        self.gui_daemon.start()
        
        self.window.show()
        self.splash.finish(self.window)
        
        # Try auto-connecting to autosampler and spectrometer.
        self.autosampler.connect()
        spinsolve_connector = threading.Thread(target=self.spinsolve_autoconnect, args=())
        spinsolve_connector.daemon = True
        spinsolve_connector.start()
        
        self.app.exec()
    
    def setup(self):
        """
        Sets up the GUI before showing the window for the first time
        """
        # setup code
        # setup logger
        eventlog = QTextEditLogger(self.window)
        eventlog.widget.setFixedHeight(200)
        self.window.verticalLayout_Status.addWidget(eventlog.widget)
        eventlog.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logging.basicConfig(level=logging.DEBUG)
        logging.getLogger().addHandler(eventlog)
        
        # define the function of buttons
        # Autosampler connect
        self.window.btn_AS_Connect.clicked.connect(self.autosampler.connect)
        # Autosampler disconnect
        self.window.btn_AS_Disconnect.clicked.connect(self.autosampler.disconnect)
        # Autosampler reset
        self.window.btn_ResetError.clicked.connect(lambda: self.autosampler.yell("r"))
        # Autosampler send error
        self.window.btn_SendError.clicked.connect(lambda: self.autosampler.yell("E"))
        # Autosampler pusher PUSH
        self.window.btn_Pusher_PUSH.clicked.connect(lambda: self.autosampler.yell("a"))
        # Autosampler pusher PULL
        self.window.btn_Pusher_PULL.clicked.connect(lambda: self.autosampler.yell("b"))
        # Autosampler air PUSH
        self.window.btn_Air_PUSH.clicked.connect(lambda: self.autosampler.yell("c"))
        # Autosampler air VENT
        self.window.btn_Air_VENT.clicked.connect(lambda: self.autosampler.yell("d"))
        # Autosampler homing
        self.window.btn_Homing.clicked.connect(lambda: self.autosampler.yell("h"))
        # Autosampler make noise
        self.window.btn_Buzz.clicked.connect(lambda: self.autosampler.yell("z"))
        # Autosampler move to position
        self.window.btn_MoveToPos.clicked.connect(lambda: self.yell_with_position("m"))
        # Autosampler measure sample
        self.window.btn_MeasureSample.clicked.connect(lambda: self.yell_with_position("M"))
        # Autosampler return sample
        self.window.btn_ReturnSample.clicked.connect(lambda: self.yell_with_position("R"))
        # Autosampler Yell
        self.window.lineEdit_Yell.returnPressed.connect(lambda: self.autosampler.yell(self.window.lineEdit_Yell.text()))
        self.window.btn_Yell.clicked.connect(lambda: self.autosampler.yell(self.window.lineEdit_Yell.text()))
        # Spinsolve connect
        self.window.btn_Spinsolve_Connect.clicked.connect(self.spinsolve.connect)
        # Spinsolve disconnect
        self.window.btn_Spinsolve_Disconnect.clicked.connect(self.spinsolve.disconnect)
        # open xampp-control.exe
        self.window.btn_open_xampp_control.clicked.connect(self.mysql_reader.open_xampp_control)
        
        # Setup Web Browser for Autosampler Table
        browser = QWebEngineViewFiltered()
        browser.load(QUrl("http://localhost/Autosampler"))
        self.window.gridLayout_Table.addWidget(browser)
        # bind F5 key to reload
        self.window.shortcutF5 = QtWidgets.QShortcut(QtGui.QKeySequence("F5"), self.window)
        self.window.shortcutF5.activated.connect(browser.reload)
        # bind backspace key to back
        self.window.shortcutBackspace = QtWidgets.QShortcut(QtGui.QKeySequence("Backspace"), self.window)
        self.window.shortcutBackspace.activated.connect(browser.back)
        
        # set window icon
        self.window.setWindowIcon(QtGui.QIcon("NMR_logo.png"))
        # this nonsense is necessary to get a nice icon in Windows 
        # see also: https://stackoverflow.com/questions/1551605/how-to-set-applications-taskbar-icon-in-windows-7
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("give me icon")
        
        # maximize window
        self.window.showMaximized()
    
    def loop(self):
        """
        Always running in background and checking the status of the components
        """
        run = True
        while run:
            try:
                config = self.mysql_reader.read_config()
                
                # work ourselves through the labels
                # autosampler com port
                if self.autosampler.port != config["ASPort"]:
                    # oh look the port was changed in the meantime
                    self.autosampler.port = config["ASPort"]
                self.refresh_label(self.window.label_AS_COMPort, self.autosampler.port)
                
                # autosampler connection status
                self.refresh_connection_label(self.window.label_AS_Connection, self.autosampler.is_connected())
                
                # autosampler error code
                self.refresh_errorcode()
                
                # autosampler last contact
                last_contact = self.autosampler.last_contact
                if last_contact == 0:
                    lastcontactstring = "Unknown"
                else:
                    lastcontactdatetime = datetime.fromtimestamp(last_contact)
                    lastcontactstring = lastcontactdatetime.strftime("%d.%m.%Y %H:%M:%S")
                    lastcontactstring += " (" + str(int(time.time() - last_contact)) + " seconds ago)"
                self.refresh_label(self.window.label_AS_LastContact, lastcontactstring)
                
                # spinsolve NMRIP & Port
                self.refresh_label(self.window.label_Spinsolve_NMRIP, config["NMRIP"] + ":" + str(config['NMRPort']))
                
                # spinsolve connection status
                self.refresh_connection_label(self.window.label_Spinsolve_Connection, self.spinsolve.is_connected())
                
                # run the "heavy" functions once per second only to save computational power
                if self.heavy_counter > 60:
                    self.heavy_counter = 0
                    # check for apache and mysql
                    self.refresh_webserver_status()
                
                # wait for 16 ms (=60 FPS)
                self.heavy_counter += 1
                time.sleep(0.016)
            except RuntimeError:
                run = False
    
    def refresh_errorcode(self):
        """
        Refreshes the label for the autosampler's status "ASStatus"
        Also refreshes the bgcolor of the label according to errorcode
        """
        label = self.window.label_AS_Status
        errorcode = self.autosampler.errorcode
        errorstring = str(errorcode) + " [" + self.autosampler.errorcodelist[errorcode] + "]"
        if label.text() != errorstring:
            # needs to change
            if errorcode == 0:
                bgcolor = "lightgreen"
            elif errorcode == 1 or errorcode == 3:
                bgcolor = "yellow"
            elif errorcode == 2 or errorcode > 3:
                bgcolor = "lightpink"
            else:
                bgcolor = "transparent"
            label.setStyleSheet("background-color: " + bgcolor + ";")
            label.setText(errorstring)
        
    def refresh_label(self, label, set_value):
        """
        Refreshes a certain label. First compares the current value to the one 
        it should have. Only if there is a difference, it will change it.
        """
        if label.text() != set_value:
            label.setText(set_value)
            
    def refresh_connection_label(self, label, set_value):
        """
        Refreshes a label which contains a connection status.
        Parameter set_value should be True or False (connected or not?)
        """
        if set_value:
            text = "\u2713 Connected"
            bgcolor = "lightgreen"
        else:
            text = "\u2717 Disconnected"
            bgcolor = "lightpink"
        if label.text() != text:
            label.setText(text)
            label.setStyleSheet("background-color: " + bgcolor + ";")
        
    def refresh_webserver_status(self):
        """
        Refreshes the apache and mysql status
        """
        label = self.window.label_Apache
        if self.mysql_reader.is_apache_running():
            text = "\u2713 Running"
            bgcolor = "lightgreen"
        else:
            text = "\u2717 Stopped"
            bgcolor = "lightpink"
        if label.text() != text:
            label.setStyleSheet("background-color: " + bgcolor + ";")
            label.setText(text)
        label = self.window.label_MySQL
        if self.mysql_reader.is_mysqld_running():
            text = "\u2713 Running"
            bgcolor = "lightgreen"
        else:
            text = "\u2717 Stopped"
            bgcolor = "lightpink"
        if label.text() != text:
            label.setStyleSheet("background-color: " + bgcolor + ";")
            label.setText(text)
    
    def yell_with_position(self, command):
        """
        Yell the command but append the holder number from the text field lineEdit_Position.
        Checks whether a number is in the lineEdit_Position first.
        """
        position = self.window.lineEdit_Position.text()
        if position.isdigit() and int(position) <= 32:
            self.autosampler.yell(command + position)
        else:
            messagebox = QtWidgets.QMessageBox()
            messagebox.setIcon(QtWidgets.QMessageBox.Critical)
            messagebox.setText("Please enter a holder number.")
            messagebox.setStandardButtons(QtWidgets.QMessageBox.Ok)
            messagebox.exec()
            
    def spinsolve_autoconnect(self):
        """
        Tries to connect to Spinsolve a multiple times... in the case that it just takes a little 
        more time to load...
        """
        connected = self.spinsolve.connect()
        i = 0
        # Wait for Spinsolve software to startup
        while not connected:
            if i > 10:
                logging.warning("No connection to Spinsolve software possible (timeout). Check if Spinsolve is running & try again.")
                break
            connected = False
            i += 1
            logging.info("Connection to the Spinsolve software failed, waiting for 10 seconds...")
            time.sleep(10)
            connected = self.spinsolve.connect()
        if connected:
            logging.info("Connection to the Spinsolve software successful!")
    
        
class QTextEditLogger(logging.Handler, QtCore.QObject):
    """
    A GUI logger for pyqt5. Thanks to everyone at StackOverflow.com
    https://stackoverflow.com/questions/28655198/best-way-to-display-logs-in-pyqt
    """
    appendPlainText = QtCore.pyqtSignal(str)
    
    def __init__(self, parent):
        super().__init__()
        QtCore.QObject.__init__(self)
        self.widget = QtWidgets.QPlainTextEdit(parent)
        self.appendPlainText.connect(self.widget.appendPlainText)
        self.widget.setReadOnly(True)
        self.widget.setStyleSheet("background-color: black; font-family: Courier; color: white;")

    def emit(self, record):
        msg = self.format(record)
        self.appendPlainText.emit(msg)
        
class QWebEngineViewFiltered(QtWebEngineWidgets.QWebEngineView):
    """
    a modified QWebEngineView which disallows any request outside of localhost
    """
    def __init__(self):
        super().__init__()
        self.urlChanged.connect(self.handleUrlChanged)
        
    def handleUrlChanged(self):
        host = self.url().host()
        if host != "localhost" and host != "127.0.0.1":
            self.stop()
            self.load(QUrl("http://localhost/Autosampler"))
   

        
        