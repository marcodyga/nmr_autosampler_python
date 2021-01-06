import code
import os
import subprocess
import time
import threading
import logging
from datetime import datetime
from MySQLReader import *

class AcdMacro:
    """
    Runs ACD macro as a non-blocking thread
    """
    
    def __init__(self, mysql_reader, fname, method_id):
        self.mysql_reader = mysql_reader
        self.fname = fname
        self.method_id = method_id
        
        self.running = False

    def run_macro(self):
        if not self.running:
            self.running = True
            macro_thread = threading.Thread(target=self.macro, args=())
            macro_thread.start()

    def macro(self):
        try:
            config = self.mysql_reader.read_config()
            
            # Check if acd folder is correctly configured
            if config["ACDFolder"] is not None and config["ACDFolder"] != "":
                specman_path = config["ACDFolder"] + "/specman.exe"
            else:
                logging.debug("ACDFolder is not configured. Automatic evaluation skipped.")
                self.running = False
                return False
            if not os.path.isfile(specman_path):
                logging.warning("ACD NMR Processor was not found at the specified location.")
                self.running = False
                return False
            
            fname = self.fname # So I don't have to write self all the time

            method = None
            standard_peaks = None
            starting_material_peaks = None
            product_peaks = None
            conn, cur = self.mysql_reader.connect_db()
            # if mysql connection can not be established, crash and die
            if conn is None or cur is None:
                return False
            if self.method_id is not None and self.method_id != 0:
                # Get config, method & peaks from DB
                cur.execute("SELECT * FROM methods WHERE ID = " + str(self.method_id))
                method = cur.fetchall()[0]
                cur.execute("SELECT * FROM peaks WHERE method = " + str(method["ID"]) + " AND role = 0")
                standard_peaks = cur.fetchall()
                cur.execute("SELECT * FROM peaks WHERE method = " + str(method["ID"]) + " AND role = 1")
                starting_material_peaks = cur.fetchall()
                cur.execute("SELECT * FROM peaks WHERE method = " + str(method["ID"]) + " AND role = 2")
                product_peaks = cur.fetchall()
                conn.close()

            # Build macro file

            # file name for the actual file must not contain a dot, otherwise ACD will not work
            fname1 = fname.replace(".", "_")

            # for now only handle the case of 1 internal standard
            # in the future, might implement multiple standards as a loop (for standard_peak in standard_peaks)

            # General stuff & Internal standard
            if standard_peaks is not None and len(standard_peaks) >= 1:
                standard = standard_peaks[0]
            else:
                standard = None
                
            if method is None or method["LB"] is None:
                methodLB = "0.2"
            else:
                methodLB = str(method["LB"])

            macro = ('ACD/MACRO <1D NMR> v12.01(14 Oct 2020 by "Marco")\r\n'
                    'CheckDocument (Type = "FID"; Nucleus = "19F")\r\n'
                    'ZeroFilling (PointsCount = "131072")\r\n'
                    'WindowFunction (Method = "Exponential"; LB = ' + methodLB + ')\r\n'
                    'FT (Operation = "Default")\r\n'
                    'Phase (Method = "Simple")\r\n')
            
            if method is not None:
                if method["BaseLine"] == "SpAveraging":
                    macro += 'BaseLine (Range = Full; Method = "' + method["BaseLine"] + '"; BoxHalfWidth = ' + str(method["BoxHalfWidth"]) + '; NoiseFactor = ' + str(method["NoiseFactor"]) + ')\r\n'
                elif method["BaseLine"] == "FIDReconstruction":
                    macro += 'BaseLine (Range = Full; Method = "' + method["BaseLine"] + '")\r\n'
            else:
                # default SpAveraging BHW=50 / NF=3
                macro += 'BaseLine (Range = Full; Method = "SpAveraging"; BoxHalfWidth = 50; NoiseFactor = 3)\r\n'
                    
            macro += 'PeakPicking (Range = Full; NoiseFactor = 6; Threshold = "SignalNoise"; MinSN = 20; PosPeaks = True; NegPeaks = True; EqualPosition = True; UseDerivation = True)\r\n'

            if standard is not None:
                macro += ('FindPeak (Position = ' + str(standard["reference_ppm"]) + '; Tolerance = ' + str(standard["reference_tolerance"]) + '; Property = "AbsHeight"; Criteria = "Maximal"; Range = 0.0000..0.0000; Value = 0.0000; IgnoreAnnotated = False; Result = IntStand)\r\n'
                          'Reference (OldPosition = $(IntStand); NewPosition = ' + str(standard["reference_ppm"]) + '; Name = "' + standard["annotation"] + '")\r\n')

            # Peaks
            if method is not None:
                for peak in starting_material_peaks + product_peaks:
                    macro += 'Integration (Method = "SelectedInterval"; Range = ' + str(peak["begin_ppm"]) + '..' + str(peak["end_ppm"]) + '; RefValue = 1.0000)\r\n'
                    macro += 'Annotation (Range = ' + str(peak["begin_ppm"]) + '..' + str(peak["end_ppm"]) + '; Text = "' + peak['annotation'] + '"; Layer = 1)\r\n'

            # Set last integral to the internal standard
            if standard is not None:
                ref_value = 100 * standard["nF"] * standard["Eq"]
                macro += 'Integration (Method = "SelectedInterval"; Range = ' + str(standard["begin_ppm"]) + '..' + str(standard["end_ppm"]) + '; RefValue = ' + str(ref_value) + ')\r\n'

            # Jcamp export
            macro += 'ExportDocument (Format = "JCAMP"; Dir = "' + config["NMRFolder"] + fname + '"; FileName = "' + fname1 + '.jdx"; IfExist = Overwrite; Setup=False)\r\n'
            # pdf export
            macro += 'ExportReportToPDF (Dir = "' + config["NMRFolder"] + fname + '"; FileName = "' + fname1 + '.pdf"; ReportType = "Template"; TemplateFile = "' + os.getcwd() + '\\19f.sk2")\r\n'
            # Save as ESP
            macro += 'SaveDocument (Dir = "' + config["NMRFolder"] + fname + '"; FileName = "' + fname1 + '.esp"; IfExist = "Overwrite")\r\n'
            
            macro += 'Execute (Application = ">taskkill"; Parameters = "/IM specman.exe"; Mode = "continue"; Hidden = false)'

            makro_file = open("makro.mcr", "w")
            makro_file.write(macro)
            makro_file.close()
            # timeout of 20 seconds for specman to finish (otherwise there may be an error in the macro execution)
            fidfile = config["NMRFolder"] + fname + "/nmr_fid.dx"
            macrofile = os.getcwd() + "/makro.mcr"
            logging.debug("Begin ACD macro")
            macro_process = subprocess.Popen(specman_path + " /SP" + fidfile + " /m" + macrofile + " /nobanner")
            timed_out = False
            for i in range(60): # 60 seconds
                if macro_process.poll() is not None:  # process just ended
                    break
                time.sleep(1)
            else:
                timed_out = True
                logging.warning("ACD macro timed out!")
            logging.debug("End ACD macro")
            success = False
            if not timed_out:
                SuccessFile = config["NMRFolder"] + fname + "/" + fname1 + ".pdf"
                #print("SuccessFile = " + SuccessFile)
                # additional timeout of 5 seconds for the pdf to appear
                timeout = 50
                while timeout > 0:
                    timeout = timeout - 1
                    if os.path.isfile(SuccessFile):
                        success = True
                        break
                    time.sleep(0.1)
            macro_process.kill()
            # Need to also kill the auto-reloading "feature" of ACD, otherwise the macro will not finish properly
            subprocess.run("taskkill /f /im SPECMAN.EXE", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run("taskkill /f /im CHEMSK.EXE", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run("taskkill /f /im ACDHOST.EXE", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            if method is not None:
                # Now open the jdx and get the integral data
                yields = []
                conversions = []
                if standard is not None:
                    jdx_filename = config["NMRFolder"] + fname + "/" + fname1 + ".jdx"
                    if os.path.isfile(jdx_filename):
                        integral_block_found = False
                        jdx_file = open(jdx_filename, "r")
                        while (line := jdx_file.readline()) != "":
                            if line.startswith("##$INTEGRALS=ACDTABLE(X1,X2,LogValue)"):
                                # Found the Integral block, now get all the integrals.
                                integral_block_found = True
                                break
                        peak_integrals = {}
                        if integral_block_found:
                            while (line := jdx_file.readline()) != "":
                                # Break when next 'data-label' "##", or EOF is reached
                                if line.startswith("##"):
                                    break
                                else:
                                    if line.startswith("("):
                                        splitted_line = line.replace("(", "").replace(")", "").split(",")
                                        # find peak
                                        for peak in starting_material_peaks + product_peaks + standard_peaks:
                                            if peak["begin_ppm"] == float(splitted_line[0]) and peak["end_ppm"] == float(splitted_line[1]):
                                                peak_integrals[peak["ID"]] = float(splitted_line[2])
                                                break
                        jdx_file.close()
                    
                    # Calculate yield & conversion
                    for peak in product_peaks:
                        if peak["ID"] in peak_integrals:
                            yld = peak_integrals[peak["ID"]] * peak["Eq"] / peak["nF"] # the yield
                            yields.append([peak, yld])
                    for peak in starting_material_peaks:
                        if peak["ID"] in peak_integrals:
                            rem_percent = peak_integrals[peak["ID"]] / (peak["Eq"] * peak["nF"]) # remaining starting material
                            conv = 100 - rem_percent # conversion
                            conversions.append([peak, conv])
                
                # Generate Report.TXT for Sciformation ELN.
                report_filename = config["NMRFolder"] + fname + "/Report.TXT"
                report_string = ("###################################\n"
                                 "# REPORT OF AUTOMATIC INTEGRATION #\n"
                                 "###################################\n"
                                 "Date: " + datetime.now().strftime("%d.%m.%Y %H:%M:%S") + "\n"
                                 "Sample: " + fname + "\n"
                                 "Method: " + method["Name"] + " (ID: " + str(method["ID"]) + ")\n")
                if standard is not None:
                    report_string += "Internal standard: " + standard["annotation"] + " @ " + str(standard["reference_ppm"]) + " ppm\n"
                if yields:
                    report_string += "\nYIELDS:\n"
                    for yld in yields:
                        report_string += "{:>20}: {:.2f}".format(yld[0]["annotation"], yld[1])
                        report_string += "% @ {:.2f} ppm\n".format((yld[0]["begin_ppm"] + yld[0]["end_ppm"]) / 2)
                if conversions:
                    report_string += "\nCONVERSIONS:\n"
                    for conv in conversions:
                        report_string += "{:>20}: {:.2f}".format(conv[0]["annotation"], conv[1])
                        report_string += "% @ {:.2f} ppm\n".format((conv[0]["begin_ppm"] + conv[0]["end_ppm"]) / 2)
                report_file = open(report_filename, "w")
                report_file.write(report_string)
                report_file.close()
                        
                # Write to DB.
                if standard is not None:
                    result_string = ""
                    if yields:
                        result_string += "Yield: "
                        first = True
                        for yld in yields:
                            if not first:
                                result_string += "/"
                            else:
                                first = False
                            result_string += str(round(yld[1]))
                        result_string += "%. "
                    if conversions:
                        result_string += "Conv.: "
                        first = True
                        for conv in conversions:
                            if not first:
                                result_string += "/"
                            else:
                                first = False
                            result_string += str(round(conv[1]))
                        result_string += "%. "
                else:
                    result_string = "n.d."
                self.mysql_reader.write_result(fname, result_string)
            
            self.running = False
        except:
            #just making sure that it always finishes
            self.running = False
        return True


