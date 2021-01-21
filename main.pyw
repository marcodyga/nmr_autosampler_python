import code
import logging
import time
import sys
from Autosampler import *
from Spinsolve import *
from MySQLReader import *
from Queue import *
from Gui import *

###########################################################
#           "AUTOSAMPLER SATAN" CONTROL PROGRAM           #
#   Part of the Autosampler project at the Gooßen group,  #
# Chair for Organic Chemistry I, Ruhr-Universität Bochum. #
#  Developed 2019-2021 by Marco Dyga, marco.dyga@rub.de   #
###########################################################

# The only thing which cannot go into the config table in mysql is the mysql server itself
# The location of XAMPP is only needed for the GUI to allow starting the XAMPP control panel via button
xampp_location = "C:/xampp"
# And the MySQL userdata
mysql_uname = "root"
mysql_passwd = ""
mysql_host = "localhost"
mysql_db = "autosampler"

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(message)s")

# display loading screen
gui = Gui()

mysql_reader = MySQLReader(mysql_uname, mysql_passwd, mysql_host, mysql_db, xampp_location)
# start apache and mysql if not yet running
if not mysql_reader.is_apache_running():
    mysql_reader.start_apache()
if not mysql_reader.is_mysqld_running():
    mysql_reader.start_mysqld()

# wait up to 1 minute until a connection to mysql can be established
for i in range(60):
    conn, cur = mysql_reader.connect_db()
    if conn is not None and cur is not None:
        conn.close()
        break
    time.sleep(1)
else:
    sys.exit("Unable to connect to mysql server.")

config = mysql_reader.read_config()

autosampler = Autosampler(mysql_reader)

spec = Spinsolve(mysql_reader)

queue = Queue(autosampler, spec, mysql_reader)

gui.initialize(queue, xampp_location)

#code.interact(local=locals())







