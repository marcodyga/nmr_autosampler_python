# Python Code for NMR Autosampler

## Introduction

The "NMR-Killer" is a 3D-printed autosampler for the [Magritek Spinsolve](https://magritek.com/products/spinsolve/) benchtop NMR spectrometer, based on Arduino, Python, and PHP.

This repository contains the **Python** code for the "NMR-Killer".

## Third-party software

The python scripts require [XAMPP](https://www.apachefriends.org/de/index.html) and a fully set up MySQL database (see the webinterface's Github page, to be added later).
The automatic evaluation of NMR spectra (optional) uses the ACD NMR Processor Academic Edition (Version 12.01).

The following libraries are required for this program:

| Library       | Licence | Weblink                                 |
| --------------|---------|-----------------------------------------|
| pyserial      | BSD     | https://pypi.org/project/pyserial/      |
| PyQt5         | GPLv3   | https://pypi.org/project/PyQt5/         |
| PyQtWebEngine | GPLv3   | https://pypi.org/project/PyQtWebEngine/ |
| mysqlclient   | GPLv2   | https://github.com/PyMySQL/mysqlclient  |

The libraries *pyserial*, *PyQt5* and *PyQtWebEngine* can simply be installed using pip:

```
pip install [packagename]
```

The mysqlclient library can not be easily installed in this fashion on Windows. It will complain about missing Visual C++ instead, even if Visual C++ has been painstakingly set up. 
This problem can be circumvented by using a precompiled package ("wheel") which is available under the following URL: https://www.lfd.uci.edu/~gohlke/pythonlibs/
After downloading the .whl file, it can be installed using the following command:

```
pip install mysqlclient-[VERSION].whl
```

## Licence

This code is available under the conditions of [GNU General Public Licence version 3](https://www.gnu.org/licenses/gpl-3.0.en.html) or any later version.