from asyncio.constants import LOG_THRESHOLD_FOR_CONNLOST_WRITES
from olexFunctions import OlexFunctions
OV = OlexFunctions()
import os
import olex_core
import olex
import olx
import math
import re
import shutil
from return_WL_energy import ret_wl
from PluginTools import PluginTools as PT

debug = bool(OV.GetParam("olex2.debug", False))

try:
  from_outside = False
  p_path = os.path.dirname(os.path.abspath(__file__))
except:
  from_outside = True
  p_path = os.path.dirname(os.path.abspath("__file__"))

l = open(os.sep.join([p_path, 'def.txt'])).readlines()
d = {}
for line in l:
  line = line.strip()
  if not line or line.startswith("#"):
    continue
  d[line.split("=")[0].strip()] = line.split("=")[1].strip()

p_name = d['p_name']
p_htm = d['p_htm']
p_img = eval(d['p_img'])
p_scope = d['p_scope']

OV.SetVar('SISYPHOS_plugin_path', p_path)

class FAPJob:                                   # one FAPjob manages the refinement, logging and output of one data set of the same structure as all other jobs
    def __init__(self, base_path = "", solution_name = "", name = "", resolution= "", energy_source = "", nos2_dict=None, indiv_disps = False, disp = False, disp_source = "", elements=None, nos2 = False, growed = False, benchmark = False):
      if nos2_dict is None:
        nos2_dict = {}
      if elements is None:
        elements = []
      self.base_path = base_path              #variable for the path to the solution .ins file for this structure
      self.solution_name = solution_name      #name of the copied ins for the structure solution
      self.name = name                        #base name
      self.energy_source = energy_source      #where does energy comes from? is used in setup ins to generate final ins thats being used
      self.resolution = resolution            #resolution limit in format SHEL 99 self.resolution
      self.growed = growed                    #does the molecule need to be grown?
      self.disp = disp                        #bool if the job is a dispersion refinement job or not
      self.disp_source = disp_source          #list of dispersion source alternatives, possible are sasaki, henke, brennan, refined
      self.indiv_disps = indiv_disps
      self.elements = elements                #lists for elements that should get refined for dispersion
      self.nos2 = nos2                        #decide whether NoS2 is being used
      self.benchmark = benchmark
      self.nos2_dict = nos2_dict              #all parameters from nosphera2 settings
      self.final_ins_path = ""                #will be set depending on other params in method setup ins
      self.refine_results = {
        "max_peak"  : 0.0,
        "max_hole"  : 0.0,
        "res_rms"   : 0.0,
        "goof"      : 0.0,
        "max_shift_over_esd" : 0.0,
        "hooft_str" : 0.0,

      }
      if base_path != "":
        self.log_sth(f"\n++++++++++++++++++++++++++++++++++\nCreated object {self.name}!\n")    #logging progress
        for attr in dir(self):
          if attr.startswith("__"):
            continue
          self.log_sth("obj.%s = %r" % (attr, getattr(self, attr)))
        self.log_sth(f"Nosphera2 properties: \t {nos2_dict}")

    def __str__(self) -> str:
      return f'Job with following options:\n\tname:\t{self.name}\n\tbase_path:\t{self.base_path}\n\tnos2:\t{self.nos2}\n\tdisp:\t{self.disp}\n'
    
    def __repr__(self) -> str:
      return f'<FAPJob Object {self.name} nos2:{self.nos2} disp:{self.disp}>'

    def log_sth(self, log:str) -> None:
      """Writes given string to the log file of the base_path

      Args:
          log (str): String to be written in the log
      """
      msg = f"{self.name}:\t{log}\n"
      with open(os.path.join(self.base_path,"log.dat"), "a") as out:
        out.write(msg)
      with open(os.path.join(self.base_path,"log.txt"), "a") as main_out:
        main_out.write(msg)

    def refine(self) -> None:
      """ Runs the refinement using predefined settings
      """
      try:
        olex.m(f"reap {self.final_ins_path}")
        self.log_sth(f"Was able to load .ins: {self.final_ins_path}")
        self.log_sth("=========================== Starting New Refinment ===========================")
        olx.AddIns("EXTI")
        olx.AddIns("ACTA")
        if self.resolution > 0:
          olex.m(f"SHEL 99 {self.resolution}")
        if self.disp:
          if self.disp_source != "refined":
            olex.m(f"gendisp -force -source={self.disp_source}")
            self.log_sth(fr"{self.name}:\t Forced gendisp command with {self.disp_source} as dispersion source!\n")
          if self.disp_source == "refined":
            for elem in self.elements:
              olex.m(f"free disp ${elem}")
              if not self.indiv_disps:
                olex.m(f"same disp ${elem}")
        else:
          olex.m("fix disp -c")
        OV.SetParam('snum.NoSpherA2.use_aspherical',False)
        if OV.GetParam('sisyphos.update_weight') == True:
          OV.SetParam('snum.refinement.update_weight', True)
          self.log_sth("Refining the weighting scheme")
        else:
          OV.SetParam('snum.refinement.update_weight', False)
          self.log_sth("keeping weighting scheme")
        olex.m("spy.set_refinement_program(olex2.refine, Gauss-Newton)")
        self.log_sth("Set refinement engine olex2.refine with G-N")
        if self.growed:
          olex.m("grow")
        for _ in range(3):
          olex.m("refine 5")
        exti = olx.xf.rm.Exti()
        self.log_sth(f"Found Extinction: {exti}")
        if exti != "n/a":
          if float(exti.split("(")[0]) < 0.001:
            olex.m("delins EXTI")
            self.log_sth(f"Deleted EXTI with exti of: {exti}")
          else:
            self.log_sth("Exti > 0.001, set L-M instead of G-N")
            olex.m("spy.set_refinement_program(olex2.refine, Levenberg-Marquardt)")
        olex.m("refine 10")
        if self.nos2:
          olex.m("neutronhdist")
          self.log_sth("H atoms placed to neutron distances (NeutronHDist command)")
          self.configure_ORCA()
          olex.m("refine 10")
        counter = 0
        self.log_sth(f'Final Shift: {abs(OV.GetParam("snum.refinement.max_shift_over_esd"))}')
        while abs(OV.GetParam("snum.refinement.max_shift_over_esd")) > 0.005:
          olex.m("refine 20")
          counter += 1
          if counter > 15:
            self.log_sth("Did not converge after 15 LS cycles, aborting and reporting results.")
            break
      except Exception as error:
        self.log_sth(str(error))
        self.log_sth("Failed during refinenement!")

    def configure_ORCA(self) -> None:
      olx.xf.EndUpdate()
      if OV.HasGUI():
        olx.Refresh()
      OV.SetParam('snum.NoSpherA2.use_aspherical',True)
      OV.SetParam('snum.NoSpherA2.source','ORCA 5.0')
      OV.SetParam('snum.NoSpherA2.precise_output',True)
      for key in self.nos2_dict:
        OV.SetParam(f'snum.NoSpherA2.{key}', f"{self.nos2_dict[key]}")
        self.log_sth(f"{key}: {OV.GetParam(f'snum.NoSpherA2.{key}')}")
      if OV.GetParam('snum.NoSpherA2.multiplicity') == '0':
        self.log_sth("I wil set a Multiplicity of 1, since none selected")
        OV.SetParam('snum.NoSpherA2.multiplicity', "1")


    def extract_info(self) -> None:
      try:
        l = ['a', 'b', 'c', 'alpha', 'beta', 'gamma']
        d = {}
        for x in l:
          val = olx.xf.uc.CellEx(x)
          d[x] = val
        d['volume'] = olx.xf.uc.VolumeEx()
        d['Z'] = olx.xf.au.GetZ()
        d['Zprime'] = olx.xf.au.GetZprime()
        stats3 = d
        self.log_sth(f"Extracted Cell stats: {stats3}")
      except Exception as error:
        self.log_sth(str(error))
        self.log_sth("Failed to extract Cell stats.")
        pass

      stats = olex_core.GetHklStat()
      self.log_sth(f"Extracted hkl stats: {stats}")

      try:
        locat = os.path.join(self.base_path,f"{self.name}.cif")
        print(locat)
        stats2 = self.parse_cif(locat)
        self.log_sth(f"Extracted cif stats: {stats2} from {locat}")
      except Exception as error:
        self.log_sth(str(error))
        self.log_sth("Failed to extract cif stats!")
        pass

      
      dist_stats = {}
      dist_errs = {}
      R1_all = 0.0
      R1_gt = 0.0
      wR2 = 0.0

      try:
        # This Block will extract the bondlengths from all bonded atoms
        use_tsc = self.nos2
        table_name = ""      
        if use_tsc == True:
          table_name = str(OV.GetParam("snum.NoSpherA2.file"))
        from refinement import FullMatrixRefine
        # Even though never used we need this import since it initializes things we need later on
        from olexex import OlexRefinementModel
        from cctbx.array_family import flex
        from scitbx import matrix
        from cctbx.crystal import calculate_distances
        #This creates the FMR with normal equations that carries EVERYTHING!
        fmr = FullMatrixRefine()
        if table_name != "":
          #Do not run refinement, simply prepare equations
          norm_eq = fmr.run(build_only=True, table_file_name=table_name)
        else:
          norm_eq = fmr.run(build_only=True)
        #and build them
        norm_eq.build_up(False)
        R1_all = norm_eq.r1_factor()[0]
        R1_gt = norm_eq.r1_factor(cutoff_factor=2.0)[0]
        wR2 = norm_eq.wR2()

        connectivity_full = fmr.reparametrisation.connectivity_table
        xs = fmr.xray_structure()

        cell_params = fmr.olx_atoms.getCell()
        cell_errors = fmr.olx_atoms.getCellErrors()
        cell_vcv = flex.pow2(matrix.diag(cell_errors).as_flex_double_matrix())
        for i in range(3):
          for j in range(i+1,3):
            if (cell_params[i] == cell_params[j] and
                cell_errors[i] == cell_errors[j] and
                cell_params[i+3] == 90 and
                cell_errors[i+3] == 0 and
                cell_params[j+3] == 90 and
                cell_errors[j+3] == 0):
              cell_vcv[i,j] = math.pow(cell_errors[i],2)
              cell_vcv[j,i] = math.pow(cell_errors[i],2)
        #Prepare the Cell Variance covariance matrix, since we need it for error propagation in distances
        cell_vcv = cell_vcv.matrix_symmetric_as_packed_u()
        sl = xs.scatterers().extract_labels()
        sf = xs.sites_frac()
        #This is VCV from refinement equations
        cm = norm_eq.covariance_matrix_and_annotations().matrix
        pm = xs.parameter_map()
        pat = connectivity_full.pair_asu_table

        # calculate the distances using the prepared information
        distances = calculate_distances(
          pat,
          sf,
          covariance_matrix=cm,
          cell_covariance_matrix=cell_vcv,
          parameter_map=pm)

        #The distances only exist once we iterate over them! Therefore build them and save them in this loop
        for i,d in enumerate(distances):
          bond = sl[d.i_seq]+"-"+sl[d.j_seq]
          dist_stats[bond] = distances.distances[i]
          dist_errs[bond] = math.sqrt(distances.variances[i])

      except Exception as error:
        print(error)
        print("Could not obtain cctbx object and calculate ESDs!\n")
        self.log_sth(str(error))
        self.log_sth("Failed to extract distances")
        pass

      with open(f"{os.path.dirname(self.base_path)}/output.txt", "a") as out:
        out.write(f"{self.name}:\n")
        out.write("Stats-GetHklStat:\t")
        for key in stats:
          out.write(str(key) + ":" + str(stats[key]) + ",")
        out.write("\nCell-Stats:\t")  
        for key in stats3:
          out.write(str(key) + ":" + str(stats3[key]) + ",")
        out.write("\nCIF-stats:\t")  
        for key in stats2:
          out.write(str(key) + ":" + str(stats2[key]) + ",")
        out.write("\nNoSpherA2_Dict:\t")
        if self.nos2 == True:
          for key in self.nos2_dict:
            out.write(str(key) + ":" + str(self.nos2_dict[key]) + ",")
        out.write("\nrefine_dict:\t")
        for key in self.refine_results:
          out.write(str(key) + ":" + str(OV.GetParam("snum.refinement."+key)) + ",")
        out.write("R1_all:" + str(R1_all) + ",R1_gt:" + str(R1_gt) + ",wR2:" + str(wR2))
        out.write("\nbondlengths:\t")
        for key in dist_stats:
          out.write(str(key) + ":" + str(dist_stats[key]) + ",")
        out.write("\nbonderrors:\t")
        for key in dist_stats:
          out.write(str(key) + ":" + str(dist_errs[key]) + ",")
        out.write("\nWeight:"+str(OV.GetParam('sisyphos.update_weight')))
        out.write("\n+++++++++++++++++++\n")
      self.log_sth(stats)
      self.log_sth(stats2)
      self.log_sth(stats3)

    def parse_cif(self, loc: str) -> dict:
      """Parses the cif given by loc and returns a dictionary of parsed information

      Args:
          loc (str): Path to the .cif file to be analyzed

      Returns:
          dict: Result dictionary from cif
      """
      print("loc", loc)
      dat_names = ["mu", 
        "wavelength", 
        "F000", 
        "tot_reflIns", 
        "goof", 
        "R_all", 
        "R1", 
        "wR2", 
        "last Shift"]

      corr_filts = ["exptl_absorpt_coefficient_mu", 
                    "diffrn_radiation_wavelength", 
                    "exptl_crystal_F_000",
                    "diffrn_reflns_number",
                    "refine_ls_goodness_of_fit_ref",
                    "refine_ls_R_factor_all",
                    "refine_ls_R_factor_gt",
                    "refine_ls_wR_factor_ref",
                    "REM Shift_max"]
      out = {}
      try:
        with open(loc, "r") as incif:
            for line in incif:
                for i,filter in enumerate(corr_filts):
                    if filter in line:
                        out[f"{dat_names[i]}"] = float(line.split()[-1])
        self.log_sth("Basic cif extraction succesfull :)")
      except:
        self.log_sth("Basic cif extraction failed!")
      try:
        with open(loc, "r") as incif:
          switch2 = False
          for line in incif:
            if self.disp == True:
              for elem in self.elements:
                switch = True                
                if  line.startswith(f" {elem} ") & switch:
                  switch = False
                  if "(" in line.split(" ")[3]:
                    print(line)
                    fp = (float(line.split(" ")[2].split("(")[0]), int(line.split(" ")[2].split("(")[1][:-1]))
                    fdp = (float(line.split(" ")[3].split("(")[0]), int(line.split(" ")[3].split("(")[1][:-1]))
                  else:
                    print(line)
                    fp = float(line.split(" ")[2])
                    fdp = float(line.split(" ")[3])
                  out[f"{elem}_anoms"] = (fp,fdp)
            if line.startswith("  _atom_site_refinement_flags_occupancy"):
              switch2 = True
              continue
            if switch2:
              if line.startswith("\n"):
                switch2 = False
              else:
                lin = line.split(" ")
                atom = lin[1]
                ueq = lin[6].split("(")[0]
                ueq_delta = lin[6].split("(")[1][:-1]
                out[f"{atom}_ueq"] = (float(ueq), int(ueq_delta))
      except Exception as e:
        self.log_sth(str(e))
        self.log_sth("Extended cif extraction failed!")
        pass
      return out

    def parse_cif2(self,loc):
      self.log_sth(f"Looking for cif information at: {loc}")
      dat_names = ["mu", 
            "wavelength", 
            "F000", 
            "tot_reflIns", 
            "goof", 
            "R_all", 
            "R1", 
            "wR2", 
            "last Shift"]

      corr_filts = ["exptl_absorpt_coefficient_mu", 
                    "diffrn_radiation_wavelength", 
                    "exptl_crystal_F_000",
                    "diffrn_reflns_number",
                    "refine_ls_goodness_of_fit_ref",
                    "refine_ls_R_factor_all",
                    "refine_ls_R_factor_gt",
                    "refine_ls_wR_factor_ref",
                    "REM Shift_max"]

      out = {}

      with open(loc, "r") as incif:
        for line in incif:
          for i,filter in enumerate(corr_filts):
            if filter in line:
              out[f"{dat_names[i]}"] = float(line.split()[-1])

      with open(loc, "r") as incif:
        for elem in self.elements:
          if self.disp == True:
            switch = True
            for line in incif:
              if  line.startswith(f" {elem} ") and switch:
                switch = False
                if "(" in line.split(" ")[3]:
                  fp = (float(line.split(" ")[2].split("(")[0]), int(line.split(" ")[2].split("(")[1][:-1]))
                  fdp = (float(line.split(" ")[3].split("(")[0]), int(line.split(" ")[3].split("(")[1][:-1]))
                  out[f"{elem}_anoms_SD"] = (fp,fdp)
                else:
                  fp = float(line.split(" ")[2])
                  fdp = float(line.split(" ")[3])
                  out[f"{elem}_anoms"] = (fp,fdp)
          incif.seek(0)
        switch2 = False
        for line in incif:              
          if line.startswith("  _atom_site_refinement_flags_occupancy") or line.startswith("  _atom_site_refinement_flags_posn"):
            switch2 = True
            continue
          if switch2:
            if line.startswith("\n"):
              switch2 = False
            else:
              try:
                lin = line.split(" ")
                atom = lin[1]
                ueq = lin[6].split("(")[0]
                ueq_delta = lin[6].split("(")[1][:-1]
                out[f"{atom}_ueq_SD"] = (float(ueq), int(ueq_delta))
              except:
                print("Skipped some Ueqs")
      return out

    def get_elements(self) -> list:
      elements = []
      for elem in str(olx.xf.GetFormula('list')).split(','):
        elements.append(elem.split(":")[0])
      return elements  

    def setupIns(self) -> None:
      self.log_sth(f"\n===========================\nbase_path:{self.base_path}\nenergy_source:{self.energy_source}\nsolution_name:{self.solution_name}\n===========================")

      if self.energy_source == "header":
        self.setupInsHeader()
      elif self.energy_source == "ins":
        self.setupInsIns()
      else:
        self.setupInsDefault()
      self.log_sth(".ins has been setup.")

    def setupInsHeader(self) -> None:     # Function for setting .ins if the energy/wl comes from the header   
      old_ins = os.path.join(self.base_path,self.name+"_old.ins")
      os.rename(os.path.join(self.base_path,self.name+".ins"),old_ins)
      with open(self.solution_name, "r") as inp, open(os.path.join(self.base_path,self.name+".ins"), "w") as out:
        energy = self.name.split("_")[-1].split(".")[0]
        try:
          wl = ret_wl(float(energy))
        except:
          self.log_sth("Filename format not fitting for Energy extraction!")
        for line in inp:
          if "CELL" in line:
            buffer = line.split(" ")
            buffer[1] = str(round(wl, 6))
            line = " ".join(buffer)
          out.write(line)
      self.correct_ins()      
      self.final_ins_path = os.path.join(self.base_path,self.name+".ins")

    def setupInsIns(self) -> None:      # Function for setting .ins if the energy/wl comes from the .ins
      old_ins = os.path.join(self.base_path,self.name+"_old.ins")
      os.rename(os.path.join(self.base_path,self.name+".ins"),old_ins)
      with open(self.solution_name, "r") as inp, open(old_ins, "r") as old_inp, open(os.path.join(self.base_path,self.name+".ins"), "w") as out:
        cell = ""
        for line in old_inp:
          if "CELL" in line:
            cell = line
        for line in inp:
          if "CELL" in line:
            line = cell
          out.write(line)      
      self.correct_ins()      
      self.final_ins_path = os.path.join(self.base_path,self.name+".ins")

    def setupInsDefault(self) -> None:
      with open(self.solution_name, "r") as inp, open(os.path.join(self.base_path,self.name+".ins"), "w") as out:
        for line in inp:
          out.write(line)
      self.correct_ins()
      self.final_ins_path = os.path.join(self.base_path,self.name+".ins")

    def correct_ins(self) -> None:
      if self.disp and self.disp_source != "refined":
        temp_ins = []
        with open(self.final_ins_path, "r") as file:
          switch = False
          for line in file:
            if line == "REM <dispersion\n":
              self.log_sth("DISPERSION line detected!")
              switch = True
              continue
            if switch:
              if "REM  <" in line:
                print(line)
                continue
              switch = False
              continue
            temp_ins.append(line)
          print(temp_ins)
        with open(self.final_ins_path, "w") as file:
            for line in temp_ins:
                file.write(line)
        self.log_sth("Corrected .ins for dispersion")  
      else: self.log_sth("Did not correct for DISP")

    def run(self) -> None:
      self.setupIns()
      if os.path.getsize(self.final_ins_path) == 0:
        self.log_sth("Failed to init the .ins, filesize = 0!")
        return
      self.log_sth("Finished Setup of INS")
      self.refine()
      self.log_sth("=========================== Finished Refinement ===========================")
      try:
        self.extract_info()
        self.log_sth("=========================== Extracted Information ===========================")
      except:
        print("Faield to extract information")
        self.log_sth("Failed to extract information!")

class SISYPHOS(PT):
  def __init__(self):
    super(SISYPHOS, self).__init__()
    self.p_name = p_name
    self.p_path = p_path
    self.p_scope = p_scope
    self.p_htm = p_htm
    self.p_img = p_img
    self.deal_with_phil(operation='read')
    self.print_version_date()
    self.base_path = ""
    self.solution_path = ""
    self.output_base_path = ""
    self.benchmarkfile_path = ""
    self.elem_string = ""
    self.ins_name = ""
    self.adjustment_eV = 0
    self.resolution = 0
    self.energy_from_header = False
    self.energy_from_ins = False
    self.use_nosphera2 = False
    self.perform_disp_ref = False
    self.growed = True
    self.benchmark = False
    self.henke = False
    self.sasaki = False
    self.brennan = False
    self.indiv_disp = False
    self.same_disp = True
    self.nos2_dict = {}
    self.outdir = ""
    if not from_outside:
      self.setup_gui()
    OV.registerFunction(self.print_formula,True,"SISYPHOS")
    OV.registerFunction(self.setBasePath,True,"SISYPHOS")
    OV.registerFunction(self.evaluate,True,"SISYPHOS")
    OV.registerFunction(self.setSolutionPath,True,"SISYPHOS")
    OV.registerFunction(self.setBenchmarkFile,True,"SISYPHOS")

  def setBenchmarkFile(self) -> None:
    out = olex.f('fileOpen("Please choose a text benchmark file", "*", filepath())')
    self.benchmarkfile_path = out
    print(f"Benchmarkfile loaded froms:\n{out}")

  def chooseDir(self) -> str:
    return olex.f('choosedir("Choose your data folder")')

  def setBasePath(self, g_path = None) -> None:
    if g_path == None:
      out= ""
      try:
        out = self.chooseDir()
      except:
        print("No directory choosen!")
      if out == "":
        print(" ")
      else:
        self.base_path = out
        print(f"Your data lies at:\n{out}")
    else:
      self.base_path = path
      print(f"Your data lies at:\n{path}")

  def setSolutionPath(self, g_path = None) -> None:
    if g_path == None:
      out = olex.f('fileOpen("Choose Your solution .ins file", "*.ins", filepath())')
      self.ins_name = os.path.basename(out)
      self.solution_path = out
      print(f"Your solution lies at:\n{out} with name {self.ins_name}")
    else:
      self.solution_path = path
      self.ins_name = os.path.basename(path)
      print(f"Your solution lies at:\n{path} with name {self.ins_name}")

  def prepare(self) -> list:  #new version 30.05.2023
    hkls_paths = {}
    joblist = []
    elements = self.elem_string.split(",")
    print(self.base_path)

    # Iterate through all files and directories in the source folder
    for root, dirs, files in os.walk(self.base_path):
        # Exclude folders named "olex2" and their subfolders
        if os.path.basename(root) == "olex2":
            continue

        # Iterate through all files in the current directory
        for file in files:
            if "SISYoutput" in root:
              continue
            elif os.path.join("olex2", "temp") in root or os.path.join("olex2", "Wfn_job") in root:
              continue
            elif file.endswith(".hkl"):              
                #source_file = os.path.join(root, file)
                name = os.path.splitext(file)[0]  # Extract the name without the extension
                hkls_paths[name] = os.path.join(root,file)
    print(hkls_paths)
    self.prepare_outdir()

    if self.energies_from_headers:
      energy_source = "header"
    elif self.energy_from_ins:
      energy_source = "ins"
    else:
      energy_source = "solution"

    for hkl in hkls_paths:
      #nos2_dict_cp = self.nos2_dict.copy()
      if self.perform_disp_ref:
        joblist.append(self.prepare_dispjob(hkl, elements, hkls_paths, energy_source))
      elif self.benchmark:
        #Add initial IAM for comparison
        joblist.append(
          self.prepare_IAM_job(hkl, elements, hkls_paths, energy_source, {})
        )
        with open(self.benchmarkfile_path, "r") as bmfp:
          for line in bmfp:
            line.strip(" ")
            if line == "\n":
              continue
            keys = line.split(";")
            keys[-1].rstrip("\n")
            new_job = self.prepare_benchmarkjob(hkl, elements, hkls_paths, energy_source, keys)
            if new_job.base_path != "":
              joblist.append(new_job)
            else: # This is the case if folder already existed, then we will not append the Job to the list
              print("!!!!Error during preparation of joblist!!!!")
              print("Skipping job with ", hkl, elements, hkls_paths, energy_source)
      else:
        joblist.append(self.prepare_defaultjob(hkl, elements, hkls_paths, energy_source))
    return joblist

  def prepare_defaultjob(self, key:str, elements, hkls_paths:dict, energy_source)-> FAPJob:
    nos2_dict_cp = self.nos2_dict.copy()
    new_dir = os.path.join(self.outdir,key)
    if os.path.exists(new_dir):
      i = 1
      while os.path.exists(new_dir):
        new_dir = new_dir + f"_{i}"
        i += 1 
    os.mkdir(new_dir)
    shutil.copy(hkls_paths[key], new_dir)
    shutil.copy(self.solution_path, os.path.join(new_dir,"solution.ins"))
    poss_ins_path = hkls_paths[key].split(".")[0]+".ins"
    print(poss_ins_path)
    if os.path.exists(poss_ins_path):
      shutil.copy(poss_ins_path, new_dir)
    hkls_paths[key] = new_dir
    return(FAPJob( 
                  base_path = new_dir, 
                  solution_name = self.solution_path, 
                  name = key, 
                  energy_source = energy_source,
                  resolution = self.resolution,  
                  disp = self.perform_disp_ref, 
                  nos2 = self.use_nosphera2, 
                  growed = self.growed,
                  nos2_dict = nos2_dict_cp.copy()
                  )
            )

  def prepare_benchmarkjob(self, key:str, elements:list, hkls_paths:dict, energy_source, keys) -> FAPJob:
    nos2_dict_cp = self.nos2_dict.copy()
    try:
      for keyy in keys:
        k, m = keyy.split(":")
        nos2_dict_cp[k.strip(" ")] = m.strip(" ").strip("\n")
    except:
      with open(os.path.join(os.path.dirname(self.base_path),"log.txt"),"a") as main_out:
        main_out.write(f"Failed to read instructions for {keys} in benchmarkfile")
    print(nos2_dict_cp)
    meth_temp =  nos2_dict_cp["basis_name"].replace('(', '').replace(')', '')
    fun_temp =  nos2_dict_cp["method"]
    new_dir = os.path.join(self.outdir,f"{key}_{fun_temp}_{meth_temp}")
    if os.path.exists(new_dir):
      i = 1
      while os.path.exists(new_dir+f"_{i}"):
        i += 1
      new_dir += f'_{i}'
    if os.path.exists(new_dir):
      return FAPJob()                                   # skip if same .hkl is found twice (different data should have a different name)
                                                        # I changed this to return an empty Job, to not have a "None" in the job list in 
                                                        # case the folder already exists, which crashes long benchmarks and is furstrating...
    os.mkdir(new_dir)
    shutil.copy(hkls_paths[key], new_dir)
    shutil.copy(self.solution_path, new_dir)
    poss_ins_path = hkls_paths[key].split(".")[0]+".ins"
    if os.path.exists(poss_ins_path):
      shutil.copy(poss_ins_path, new_dir)
    return(FAPJob(                                   # create the FAPJob object here
                          base_path = new_dir, 
                          solution_name = self.solution_path, 
                          name = f"{key}_{fun_temp}_{meth_temp}", 
                          energy_source = energy_source,
                          resolution = self.resolution,  
                          disp = self.perform_disp_ref, 
                          elements = elements,
                          nos2 = True,
                          benchmark = True, 
                          growed = self.growed,
                          nos2_dict = nos2_dict_cp.copy()
                          )  
                  )

  def prepare_IAM_job(self, key:str, elements:list, hkls_paths:dict, energy_source, keys) -> FAPJob:
    new_dir = os.path.join(self.outdir,f"{key}_IAM")
    if os.path.exists(new_dir):
      return FAPJob()                                   # skip if same .hkl is found twice (different data should have a different name)
                                                        # I changed this to return an empty Job, to not have a "None" in the job list in 
                                                        # case the folder already exists, which crashes long benchmarks and is furstrating...
    os.mkdir(new_dir)
    shutil.copy(hkls_paths[key], new_dir)
    shutil.copy(self.solution_path, new_dir)
    poss_ins_path = hkls_paths[key].split(".")[0]+".ins"
    if os.path.exists(poss_ins_path):
      shutil.copy(poss_ins_path, new_dir)
    return(FAPJob(                                   # create the FAPJob object here
                          base_path = new_dir, 
                          solution_name = self.solution_path, 
                          name = f"{key}_IAM", 
                          energy_source = energy_source,
                          resolution = self.resolution,  
                          disp = False, 
                          elements = elements,
                          nos2 = False,
                          benchmark = True, 
                          growed = self.growed,
                          nos2_dict = {}
                          )  
                  )

  def prepare_dispjob(self, key, elements, hkls_paths,energy_source) -> FAPJob:
    nos2_dict_cp = self.nos2_dict.copy()
    disp_sources = ["refined"]
    indiv_disps = False         
    if self.henke:
      disp_sources.append("henke")
    if self.sasaki:
      disp_sources.append("sasaki")
    if self.brennan:
      disp_sources.append("brennan")
    for disp_source in disp_sources:  
      if self.benchmark:                              #also check if a benchmark should be performed
        with open(self.benchmarkfile_path, "r") as bmfp:
          for line in bmfp:
            fun, meth = line.split(",")
            meth = meth.rstrip("\n")
            meth_temp =  meth.replace('(', '').replace(')', '')
            new_dir = os.path.join(self.outdir,f"{key}_{disp_source}_{fun}_{meth_temp}")
            nos2_dict_cp["basis_name"] = meth
            nos2_dict_cp["method"] = fun
            if os.path.exists(new_dir):
              i = 1
              while os.path.exists(new_dir):
                new_dir = new_dir + f"_{i}"
                i += 1                                
            os.mkdir(new_dir)
            shutil.copy(hkls_paths[key], new_dir)
            shutil.copy(self.solution_path, new_dir)
            poss_ins_path = hkls_paths[key].split(".")[0]+".ins"
            if os.path.exists(poss_ins_path):
              shutil.copy(poss_ins_path, new_dir)
            return(FAPJob(                                   # create the FAPJob object here
                                  base_path = new_dir,
                                  solution_name = self.solution_path, 
                                  name = key,
                                  energy_source = energy_source, 
                                  resolution = self.resolution,  
                                  disp = self.perform_disp_ref,
                                  indiv_disps = indiv_disps, 
                                  disp_source= disp_source, 
                                  elements = elements,
                                  nos2 = True, 
                                  growed = self.growed,
                                  nos2_dict = nos2_dict_cp.copy()
                                  )  
                          )
      else:
        new_dir = os.path.join(self.outdir,f"{key}_{disp_source}")
        if os.path.exists(new_dir):
          i = 1
          while os.path.exists(new_dir):
            new_dir = new_dir + f"_{i}"
            i += 1 
        os.mkdir(new_dir)
        shutil.copy(hkls_paths[key], new_dir)
        shutil.copy(self.solution_path, new_dir)
        poss_ins_path = hkls_paths[key].split(".")[0]+".ins"
        print(poss_ins_path)
        if os.path.exists(poss_ins_path):
            shutil.copy(poss_ins_path, new_dir)
        return(FAPJob( 
                                  base_path = new_dir, 
                                  solution_name = self.solution_path, 
                                  name = key, 
                                  energy_source = energy_source,
                                  resolution = self.resolution,  
                                  disp = self.perform_disp_ref,
                                  indiv_disps = indiv_disps,  
                                  disp_source = disp_source, 
                                  elements = elements,
                                  nos2 = self.use_nosphera2, 
                                  growed = self.growed,
                                  nos2_dict = nos2_dict_cp.copy()
                                  )
                        )

  def prepare_outdir(self) -> None:
    i = 1                                                   # add a new outputfolder
    while os.path.exists(os.path.join(self.base_path, "SISYoutput"+str(i))):
      i += 1
    self.outdir = os.path.join(self.base_path, "SISYoutput"+str(i))
    os.mkdir(self.outdir)
    self.output_base_path = self.outdir  

  def set_up_params(self) -> None:                              # Handles all settings made in the interface (SISYPHOS.htm)   
    self.elem_string = OV.GetParam("sisyphos.element_string")
    if OV.GetParam("sisyphos.adjustment_eV"):
      self.adjustment_eV = float(OV.GetParam("sisyphos.adjustment_eV"))
    if OV.GetParam("sisyphos.resolution"):
      self.resolution = float(OV.GetParam("sisyphos.resolution"))
    self.use_nosphera2 = OV.GetParam("sisyphos.use_nos2")
    self.perform_disp_ref = OV.GetParam("sisyphos.perform_disp_ref")
    if self.perform_disp_ref:
      self.indiv_disp = OV.GetParam("sisyphos.indiv_disp")
      self.indiv_disp = OV.GetParam("sisyphos.same_disp")
      self.elem_string = OV.GetParam("sisyphos.element_string")
    self.benchmark = OV.GetParam("sisyphos.benchmark_mode")
    self.henke = OV.GetParam("sisyphos.henke")
    self.sasaki = OV.GetParam("sisyphos.sasaki")
    self.brennan = OV.GetParam("sisyphos.brennan")
    self.energies_from_headers = OV.GetParam("sisyphos.energies_from_headers")
    self.energy_from_ins = OV.GetParam("sisyphos.energies_from_ins")
    self.same_disp = OV.GetParam("sisyphos.energies_from_ins")
    nos_params = ["basis_name","method", 
                  "ncpus", "mem", "charge", 
                  "multiplicity", "full_HAR",
                  "Max_HAR_Cycles","becke_accuracy",
                  "Relativistic", "h_aniso", 
                  "h_afix", "add_disp", 
                  "cluster_radius", "DIIS",
                  "cluster_grow", "ORCA_SCF_Conv",
                  "ORCA_SCF_Strategy", "ORCA_Solvation",
                  "pySCF_Damping"]
    for param in nos_params:
      self.nos2_dict[param] = OV.GetParam(f"snum.NoSpherA2.{param}")
    print(self.nos2_dict)

  def print_formula(self) -> None:   #This one does the actual work
    if self.solution_path == "" or self.base_path == "":
      print("\nPlease set up your data directory and solution first!")
    else:
      if self.benchmark == True and self.benchmarkfile_path == "":
        print("\nPlease provide a benchmark file!")
      else:
        self.set_up_params()
        joblist = self.prepare()
        print(f"Initial Joblist: {joblist}")
        print(f"Wrote results to {self.outdir}")
        print(f"NospherA2 dict: {self.nos2_dict}")

        with open(f"{os.path.dirname(self.base_path)}/log.txt","a") as main_out:
            main_out.write(f"Joblist: \t{joblist}")

        for job in joblist:
          try:
            job.run()
          except NameError as error:
            print(f"ERROR! \nDidnt (fully) run {job.name}!\nSee log for additional info.")
            print(error)
        print(joblist)
        print(f"SISYPHOS run finished, results and log in {self.base_path}")

  def evaluate(self) -> None:
    hkl_stats = ["Name","TotalReflections", "UniqueReflections", "FriedelOppositesMerged", "InconsistentEquivalents", "SystematicAbsencesRemoved", "MinD", \
    "MaxD", "LimDmin", "LimDmax", "FilteredOff", "OmittedByUser", "OmittedReflections", "IntensityTransformed", "Rint", "Rsigma", "MeanIOverSigma", \
    "Completeness", "MaxIndices", "MinIndices", "FileMaxIndices", "FileMinIndices", "ReflectionAPotMax", "FriedelPairCount", "Redundancy","a", "b", "c", \
    "alpha", "beta", "gamma", "volume", "Z", "Zprime", "mu", "F000", "TotalReflections", "wavelength", "goof", "R_all", "R1", "wR2", "DISPS", "last Shift", "Ueqs"]

    exceptions = ["MaxIndices", "MinIndices", "FileMaxIndices", "FileMinIndices", "Redundancy", "Ueqs", "DISPS"]

    with open(os.path.join(self.base_path,"output.txt"), "r") as dat:
      output = {}
      #stats = {}
      ueqs = {}
      for line in dat: 
        if ":\n" in line:
          output["Ueqs"] = ueqs
          #output = output.append(stats, ignore_index=True)
          output["Name"] = line.split(":")[0]
        for key in hkl_stats:
          if key in line:
            if key in exceptions:
              if key == "DISPS":
                output[key] = "placeholder"
              else:
                output[key] = re.findall(f"{key}:.+,",line)[0].split(")")[0].split(":")[1]+")"
            else:
              try:
                output[key] = re.findall(f"{key}:.+,",line)[0].split(",")[0].split(":")[1]
              except:
                if key == "a" or key == "b" or key == "c":
                  continue
                else: 
                  print(f"Failed at {key}")  
          if "_ueq" in line:
            line_items = line.split(")")
            for itm in line_items:
              if "_ueq" in itm:
                atom = itm.split(":")[0][1:]
                ueqs[atom] = itm.split(":")[1]+")"
      output["Ueqs"] = ueqs


SISYPHOS_instance = SISYPHOS()
