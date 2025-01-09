import copy
from datetime import datetime
from enum import Enum
from flask import Flask, render_template, request, jsonify
from FlexDB import FlexTinyDB
import json
from multiprocessing import Process
from multiprocessing import Queue   #Pipe (not process safe)
import os
import random
from threading import Thread, Event
import time
from waitress import serve

from twisted.internet import task, reactor

# Sentry
# import sentry_sdk

# sentry_sdk.init(    
#     dsn="https://  "# put dsn project key here @sentry.io/  put project number here,
#     # Set traces_sample_rate to 1.0 to capture 100%
#     # of transactions for tracing.    
#     traces_sample_rate=1.0,
#     # Set profiles_sample_rate to 1.0 to profile 100%
#     # of sampled transactions.
#     # We recommend adjusting this value in production.    
#     profiles_sample_rate=1.0,
# )

# Invoke the Flask Webserver
app = Flask(__name__)

FlexModules = {}
FlexTemplates = {}  # It now needs to be a dictionary to access the process and the pipe
FlexPlan = []

# Database
db = FlexTinyDB()

# Are we editing the layout
isEditing = False

page_timeout = 300
loop_time = 0

# Piped commands
SYNC = 0                                                                                            
SYNC_ACK = 1
GET_INFO = 2
GET_INFO_ACK = 3
GET_STATUS = 4
GET_STATUS_ACK = 5 
GET_PAGE = 6
GET_PAGE_ACK = 7
SET_PAGE = 8
SET_PAGE_ACK = 9
GET_OUTPUTS = 10
GET_OUTPUTS_ACK = 11
SET_INPUTS = 12
SET_INPUTS_ACK = 13

# Storage data for each module
class mod_data:
   mod_info = None
   mod_status = None
   mod_pageinfo = None
   mod_pagedata = None
   mod_outputs = None

# Load the templates of each module - We use these as a building blocks in runtime and edit mode when creating new instances of modules with real,
# not randomised IDs. They must be cleared after use with unload_templates() else the zombies will clash with the working modules.
def load_templates():
    for file in os.listdir("."):
        if file.startswith("FlexMod_") and file.endswith(".py"):  # Do they match our naming convention?
            
            # Create a pipe; this is how we'll be accessing the module's data
            #pipe1, pipe2 = Pipe()
            inQ = Queue(maxsize=1) 
            outQ = Queue(maxsize=1)
            
            module = __import__(os.path.splitext(file)[0])  # Strip the .py extension and import the module
            
            # Spawn the module's own process and give it the other end of the pipe and a random ID
            process_id = random.randint(1, 65535)
            module_process = Process(target=module.driver, args=([inQ, outQ],process_id))
            module_process.start()
            
            # Create a module class for storing data in
            module_data = mod_data()
            
            # Retain the process and its end of the pipe for future use (closure)
            FlexTemplates[process_id] = [module_process, [inQ, outQ], module_data]
            
# Remove copies of modules created in runtime or edit modes of operation.
def unload_templates():
    if len(FlexTemplates) > 0:
        for i in range(len(FlexTemplates)):
            FlexTemplates[i].kill()
        # Remove the templates from memory
        FlexTemplates.clear()
        #print(FlexTemplates)
        print("Flex3.5 is now running...")


# Load Modules from a given layout
def load_modules(plan):
    global FlexModules
    global FlexTemplates
    global isEditing

    # Remove any modules in the list as editing them on the HMI may reallocate IDs
    for key in FlexModules:
        
        # Remove the pipe (so it doesn't break when we stop the process)
        FlexModules[key][1][0].close()
        FlexModules[key][1][1].close()
        
        # Stop the module process
        FlexModules[key][0].terminate()
        

    # For all the modules in FlexPlan
    for module in plan["nodeDataArray"]:
        # Confirm it is a Module by a known parameter, not a group or a wire... 
        # We used to use "type", but it didn't differentiatie between vendors so they all had to be the same. Using the actual name *should* allow us to mix and match devices.
        # But we can strip the Python module name from the website string. Bit long winded but it's a large edit of the Flex HTML code otherwise.
        if "website" in module: 
            module_name = str(module["website"].lstrip("/Mod/"))
            module_name = module_name[:module_name.index("_(")]
            
            # Strip the UID from the web address.
            if "FlexMod_" in module["website"]:  # Modules with real URLs are not added to the device list...
                moduleUID = int(module["website"].split('_(')[1].split(')')[0])  # ...as there's no interaction once navigated elsewhere.
            
            # Create the processes again (we could streamline this with load_templates)
            #pipe1, pipe2 = Pipe()
            inQ = Queue() 
            outQ = Queue() 
            
            module = __import__(module_name)  # Strip the .py extension and import the module
            # Spawn the module's own process and give it the other end of the pipe and a random ID
            process_id = moduleUID
            #module_process = Process(target=module.driver, args=(pipe2,process_id))
            module_process = Process(target=module.driver, args=([inQ, outQ],process_id))
            module_process.start()
            
            # Create a module class for storing data in
            module_data = mod_data()
            
            # Retain the process and its end of the pipe for future use (closure)
            #FlexModules[process_id] = [module_process, pipe1]
            FlexModules[process_id] = [module_process, [inQ, outQ], module_data]
            
            # Test it is alive before loading the next. Processes are taking a long time to load (~3secs)
            running = False
            while not running:
                try:
                    tx_msg = [GET_INFO]
                    FlexModules[process_id][1][1].put(tx_msg)
                    rx_msg = FlexModules[process_id][1][0].get()
                    
                    if isinstance(rx_msg, list):
                        if rx_msg[0] == GET_INFO_ACK:
                            running = True          
                except:
                    pass

# Home page
@app.route("/", methods=["GET", "POST"])
@app.route("/<path>", methods=["GET", "POST"])
def home_page(path=None):
    global FlexPlan
    global FlexModules
    global FlexTemplates
    global isEditing
    global page_timeout

    data = dict()

    if request.method == "POST":

        # Finished editing
        if path == "Edit":

            FlexPlan = request.data

            if FlexPlan is not None:
                # Save the new layout to the database
                newPlan = json.loads(FlexPlan.decode('utf-8'))

                # Create new instances of the module classes with assigned IDs
                load_modules(newPlan)

                # Save the pan to the database
                db.save_to_db("FlexLayout", newPlan)

                # Clear any lists containing module data as all the IDs are now different and would get appended to the old IDs.
                isEditing = False

            return 'OK', 200

    
    elif request.method == "GET":
        
        # Home page layout
        if path == "layout":
            if FlexPlan:
                return FlexPlan
            else:
                print("no layout available")
                
            return jsonify(data)

        # Home page data
        elif path == "Home_data":
            # Status
            for key in FlexModules:                  
                
                #print(FlexModules[key][2].mod_status)
                if FlexModules[key][2].mod_status is not None:
                    data[key] = FlexModules[key][2].mod_status[1:]                                      
                
            return jsonify(data)
     
        # Edit page
        elif path == "Edit":  # Home page data
            # Disable any ongoing tasks whilst editing
            isEditing = True

            load_templates()
            return render_template("Flex3_Edit.html")

        # Edit page data
        elif path == "Edit_data":
            
            # We can call the command here as we won't conflict with the main thread in edit mode. And we only update the templates once.
            x = 0
            for key in FlexTemplates:                 # The key represents the module process, and the value represents its pipe. See load_templates  
                
                try:
                    tx_msg = [GET_INFO]
                    FlexTemplates[key][1][1].put(tx_msg, block=True)       # <-- this will be the command to read device data
                    rx_msg = FlexTemplates[key][1][0].get(timeout=5)    # <-- and this will block so be careful. Check the pipe exists before entering this code.
                
                    if isinstance(rx_msg, list):
                        if rx_msg[0] == GET_INFO_ACK:         # Check the response is what we expected
                            data[x] = rx_msg[1:]          # Strip off the response header
                    x += 1
               
                except Exception as e:
                    print(e)
                
            return jsonify(data)

        # Placeholder if not dealt with by Bootstrap
        elif path == "favicon.ico":
            pass
    
    return render_template("Flex3.html")

# Module page
@app.route("/Mod/<path:path>", methods=["GET", "POST"])
def module_page(path=None):
    global FlexModules
    global FlexTemplates
    global page_timeout
    global ack_state
    data = dict()

    if request.method == "POST":
        
        for key in FlexModules:
        
            try:
                page_data = FlexModules[key][2].mod_pageinfo
                        
                if ("/Mod/" + path) == page_data[0]:
                    
                    try:
                        
                        tx_msg = [SET_PAGE, ("/Mod/" + path), request.form]
                        FlexModules[key][1][1].put(tx_msg, block=True)
                        rx_msg = FlexModules[key][1][0].get(timeout=5)
                        
                        if isinstance(rx_msg, list):
                            if rx_msg[0] == SET_PAGE_ACK:
                                pass
                                
                    except Exception as e:
                        print(e)
                                
            except Exception as e:
                print(e)
                
        return render_template(path.split("_(")[0] + ".html")

    elif request.method == "GET":
        
        # Might be a Module Page or Module Data
        for key in FlexModules:
            
            try:
                page_data = FlexModules[key][2].mod_pageinfo                                        # The page and route data are very unlikely to change so we can update them in the main process
                
                if "/data" in path:
                    for route in range(len(page_data[1])):
                        if ("/Mod/" + path) == page_data[1][route]:
                            try:
                                tx_msg = [SET_PAGE, ("/Mod/" + path), request.form]                 # Sadly however, SET_PAGE is form-dependent and it's difficult to avoid in-place usage without adding process delays.
                                FlexModules[key][1][1].put(tx_msg, block=True)                      # ... this is definitely one to look at again sometime as it risks not loading a page or its data.
                                rx_msg = FlexModules[key][1][0].get(timeout=5)
                                
                                if isinstance(rx_msg, list):
                                    if rx_msg[0] == SET_PAGE_ACK:
                                    
                                        return jsonify(rx_msg[1])
                                        
                            except Exception as e:
                                print(e)
                                
                # Module Page
                elif ("/Mod/" + path) == page_data[0]:    
                    try:
                        tx_msg = [SET_PAGE, path, request.form]
                        FlexModules[key][1][1].put(tx_msg, block=True)       
                        rx_msg = FlexModules[key][1][0].get(timeout=5)                              # If the page doesn't return any data, flush the pipe (read), else buffered data will corrupt future tasks.
                    except Exception as e:
                        print(e)
                        
                    return render_template(path.split("_(")[0] + ".html")                           # Each module is given a unique identifier "_(number)"

                    
            except Exception as e:
                    print(e)
                    
        return jsonify(data)

# Splash screen
@app.route("/Splash/", methods=["GET"])
def splash_page(path=None):
    return render_template("FlexSplash.html")


loop_time = 0
pool_time = 0

class Interval(Thread):
    def __init__(self, event, process, interval):
        Thread.__init__(self)
        self.stopped = event
        self.target = process
        self.interval = interval
        self.interval_offset = interval
        self.interval_count = 0

    def run(self):
        global loop_time
        global pool_time
        ms_offset = 0                                                       # We divided all possible modules (25) into 1 second rotations, leaving us with 40mS per module.

        while not self.stopped.wait(self.interval_offset):
            
            #print(datetime.now())
            self.interval_offset = self.interval-loop_time
            self.target()


class Module():
    def __init__(self):
        global FlexModules
        print("Back End Running...")

        # Clear the old output data
        self.modOutputs = [None] * len(ModTypes)
        self.heartbeat = 0
        
    def process(self):
        global loop_time
        global pool_time
        s = time.time()
        
        #print("\n")
        #print(datetime.now())
        #print("(0)    Flex3 Heartbeat: " + str(self.heartbeat) + ", loop time: " + str(loop_time) + " Seconds")
        
        self.heartbeat += 1
        if self.heartbeat >= 0xFFFF:
            self.heartbeat = 0
        
        if not isEditing:
            
            self.modOutputs = [None] * len(ModTypes)                                                # Without this we reciprocate the client module because it currently needs a copy of itself.
            
            # We're populating a list of module data for the controller:                                    [[Base],[Controller],[Battery],[DIO1]]...
            # If there is a UID for multiple like modules, it is stuffed into the same location:  [[Base],[Controller],[Battery],[[DIO1],[DIO1]]]
            
            # Synchronously update each module's data. By doing it all here and not also in asynchronous methods, we ought to avoid some nasty race conditions #experience.
            for key in FlexModules:
                
                # Retrieve basic module information.
                try: 
                    #p = time.time()
                    tx_msg = [GET_INFO] 
                    FlexModules[key][1][1].put(tx_msg)
                    rx_msg = FlexModules[key][1][0].get()
                    #print("TIME: " + str(time.time()-p))
                    if isinstance(rx_msg, list):
                        if rx_msg[0] == GET_INFO_ACK:                                               # Check the response is what we expected
                            FlexModules[key][2].mod_info = rx_msg[1:]                               # Strip off the reply. We might implement a CRC too eventually for data integrity.
                except Exception as e:
                    print("Error when reading INFO from Flex Module")
                
                # Retrieve module status, displayed on the Home page.
                try:
                    tx_msg = [GET_STATUS]  
                    FlexModules[key][1][1].put(tx_msg)                    
                    rx_msg = FlexModules[key][1][0].get()   
                    
                    if isinstance(rx_msg, list):
                        if rx_msg[0] == GET_STATUS_ACK:                                             
                            FlexModules[key][2].mod_status = rx_msg[1:]
                except Exception as e:
                    print("Error when reading STATUS from Flex Module")
                
                # Retrieve a list containing the module page and data route(s)
                try:
                    tx_msg = [GET_PAGE]
                    FlexModules[key][1][1].put(tx_msg)  
                    rx_msg = FlexModules[key][1][0].get()   
                    
                    if isinstance(rx_msg, list):
                        if rx_msg[0] == GET_PAGE_ACK:  
                            FlexModules[key][2].mod_pageinfo = rx_msg[1] 
                except Exception as e:
                    print("Error when reading GET_PAGE from Flex Module")
                    
                # Set Page TODO, if it's possible to relocate them.
                
                # Get Outputs (the result of the module doing work)
                try:
                    tx_msg = [GET_OUTPUTS]
                    FlexModules[key][1][1].put(tx_msg)
                    rx_msg = FlexModules[key][1][0].get()
                    
                    if isinstance(rx_msg, list):
                        if rx_msg[0] == GET_OUTPUTS_ACK:
                            FlexModules[key][2].mod_outputs = rx_msg[1]                             # Local Module copy, convenient but may not be used.

                            # Check if *any* module data is saved in the system structure
                            mod_type = FlexModules[key][2].mod_info[1]
                            if self.modOutputs[mod_type] is None:                                   # Check if any modules of this type are present in the module list
                                self.modOutputs[mod_type] = [rx_msg[1]]
                            else:                                                                   # We have to search through the list of modules of this type to see if there is already an entry for this module
                                x = 0
                                foundUID = False
                                moduleData = self.modOutputs[mod_type]                              # Copy all modules of this type
                                for inputs in moduleData:
                                    
                                    if len(inputs) > 0:
                                        if inputs[0] == key:                                        # Check if it module data for this UID exists already
                                            foundUID = True
                                            
                                            # Overwrite the module's outputs
                                            moduleData[x] = rx_msg[1]                               # Overwrite existing data for just this UID
                                            self.modOutputs[mod_type] = moduleData   # Restore all data
                                        x += 1

                                if not foundUID:
                                    moduleData.append(rx_msg[1])                                    # New module data, append it:       [[mod0_edit],[mod1]]
                                    self.modOutputs[mod_type] = moduleData
                except Exception as e:
                    print("Error when reading GET_OUTPUTS from Flex Module")
                
            for key in FlexModules:    
                # Set SCADA Inputs (Modules doing the thinking)
                try:
                    mod_uid = FlexModules[key][2].mod_info[0]
                    mod_type = FlexModules[key][2].mod_info[1]                                      # We already know the type of this module from an earlier call to GET_INFO
                    
                    if mod_type == 22:                                                               # The controller requires knowledge of all the attached devices
                        scadaInputs = [None] * (len(ModTypes) + 2)                                  # Build the module data structure recognised by the module
                        scadaInputs[0] = mod_uid
                        scadaInputs[1] = False
                        for x in range(len(ModTypes) - 1):                                          # Feed in the module outputs starting from the battery type
                            scadaInputs[x + 2] = self.modOutputs[x]                                  # TODO: This looks incorrect, and likely overwrites ctrlInputs[1]!
                        
                        tx_msg = [SET_INPUTS, scadaInputs]                                           # We're feeding each module new data generated by the client module.
                        FlexModules[key][1][1].put(tx_msg)
                        rx_msg = FlexModules[key][1][0].get()                    
                        
                        if isinstance(rx_msg, list):
                            if rx_msg[0] == SET_INPUTS_ACK:
                                self.modOutputs = rx_msg[1]
                                                    
                except Exception as e:
                    print("Error when reading SET_INPUTS from Flex Module " + str(mod_type) + " : " + str(e))
              
            # WE NEED TO FORCE THE SCADA PROCESS HERE TO SPEED UP INVERTER SETPOINTS
            
            for key in FlexModules:                                                                 # Populating inputs only works if you have already collected the client data   
                try:
                    mod_uid = FlexModules[key][2].mod_info[0]
                    mod_type = int(FlexModules[key][2].mod_info[1])                                      # We already know the type of this module from an earlier call to GET_INFO
                    
                    # Set Device Inputs (Modules talking to hardware)
                    if (mod_type >= 2) and (mod_type <= 24):                                        # Exclude consumers of all data (ctrl / logging / scada / client)               
                        if self.modOutputs[24] is not None:                                         # We use outputs generated by the Client module to populate the device states
                            if len(self.modOutputs[24][0]) >= 24:                                        # We've got some data in the module outputs list
                                tx_msg = [SET_INPUTS, self.modOutputs[24][0][mod_type]]                 # We're feeding each module new data generated by the client module.
                                FlexModules[key][1][1].put(tx_msg)
                                rx_msg = FlexModules[key][1][0].get()                    

                                if isinstance(rx_msg, list):
                                    if rx_msg[0] == SET_INPUTS_ACK:
                                        pass
                        else:
                            print("No Client Installed!")
                
                except Exception as e:
                    print("Error when reading module SET_INPUTS from Flex Module: " + str(e))
                 
            for key in FlexModules:
                # Time sync the modules to this second
                try:    
                    tx_msg = [SYNC] 
                    FlexModules[key][1][1].put(tx_msg)
                
                except Exception as e:
                    print("Error when reading INFO from Flex Module")
              
            for key in FlexModules:
                # Set Logging Inputs (Modules doing the thinking)
                try:
                    mod_uid = FlexModules[key][2].mod_info[0]
                    mod_type = FlexModules[key][2].mod_info[1]                                      # We already know the type of this module from an earlier call to GET_INFO
                    
                    if mod_type == 23:                                                               # The controller requires knowledge of all the attached devices
                        loggingInputs = [None] * (len(ModTypes) + 2)                                  # Build the module data structure recognised by the module
                        loggingInputs[0] = mod_uid
                        loggingInputs[1] = False
                        for x in range(len(ModTypes) - 1):                                          
                            loggingInputs[x + 2] = self.modOutputs[x]                                  
                        
                        tx_msg = [SET_INPUTS, loggingInputs]                                           # We're feeding each module new data generated by the client module.
                        FlexModules[key][1][1].put(tx_msg)
                        rx_msg = FlexModules[key][1][0].get()                    
                        
                        if isinstance(rx_msg, list):
                            if rx_msg[0] == SET_INPUTS_ACK:
                                pass#self.modOutputs = rx_msg[1]  
                
                except Exception as e:
                    print("Error when reading SET_INPUTS from Logging Module: " + str(e))
                   
            for key in FlexModules:
                # Set Control Inputs (Modules doing the thinking)
                try:
                    mod_uid = FlexModules[key][2].mod_info[0]
                    mod_type = FlexModules[key][2].mod_info[1]                                      # We already know the type of this module from an earlier call to GET_INFO
                    
                    if mod_type == 1:                                                               # The controller requires knowledge of all the attached devices
                        ctrlInputs = [None] * (len(ModTypes) + 1)                                   # Build the module data structure recognised by the module
                        ctrlInputs[0] = mod_uid
                        ctrlInputs[1] = False
                        for x in range(len(ModTypes) - 2):                                          # Feed in the module outputs starting from the battery type
                            ctrlInputs[x + 1] = self.modOutputs[x + 1]                                  # TODO: This looks incorrect, and likely overwrites ctrlInputs[1]!
                        
                        tx_msg = [SET_INPUTS, ctrlInputs]                                           # We're feeding each module new data generated by the client module.
                        FlexModules[key][1][1].put(tx_msg)
                        rx_msg = FlexModules[key][1][0].get()                    
                        
                        if isinstance(rx_msg, list):
                            if rx_msg[0] == SET_INPUTS_ACK:
                                self.modOutputs = rx_msg[1]
                                
                except Exception as e:
                    print("Error when reading SET_INPUTS from Flex Module: " + str(e))
            print(len(FlexModules))
            for key in FlexModules:
                # Set Client Inputs (Modules doing the thinking)
                try:
                    mod_uid = FlexModules[key][2].mod_info[0]
                    mod_type = FlexModules[key][2].mod_info[1]                                      # We already know the type of this module from an earlier call to GET_INFO
                    
                    if mod_type == 24:                                                              # The controller requires knowledge of all the attached devices
                        clientInputs = [None] * (len(ModTypes) + 2)                                 # Build the module data structure recognised by the module
                        clientInputs[0] = mod_uid
                        clientInputs[1] = False
                        
                        for x in range(len(ModTypes) - 1):                                          # Feed in the module outputs starting from the battery type
                            clientInputs[x + 2] = self.modOutputs[x]                                # TODO: This looks incorrect, and likely overwrites
                        
                            tx_msg = [SET_INPUTS, clientInputs]                                     # We're feeding each module new data generated by the client module.
                            FlexModules[key][1][1].put(tx_msg)
                            rx_msg = FlexModules[key][1][0].get()                    

                            if isinstance(rx_msg, list):
                                if rx_msg[0] == SET_INPUTS_ACK:
                                    pass                      
                except Exception as e:
                    print("Error when reading client SET_INPUTS from Flex Module: " + str(e))
        
        pool_time = time.time()
        loop_time = time.time() - s
        
     
class ModTypes(Enum):
    BASE = 0
    CONTROL = 1
    BATTERY = 2
    INVERTER = 3
    AC_METER = 4
    DC_METER = 5
    DIG_IO = 6
    ANA_IO = 7
    MIXED_IO = 8
    SWITCH = 9
    LI_ION = 10
    DCDC = 11
    AIRCON = 12
    SENSOR = 13
    FUEL_CELL = 14
    AC_GEN = 15
    AC_WIND = 16
    AC_SOLAR = 17
    DC_SOLAR = 18
    AC_EFM = 19
    DC_EFM = 20
    EV_CHARGE = 21
    SCADA = 22      
    LOGGING = 23
    CLIENT = 24
    UNDEFINED = 25
    
    
def frontEnd():
    # Serve using a production WSGI server
    serve(app, host="0.0.0.0", port=8080, threads=10)
    
    
if __name__ == '__main__':
    
    print("Flex3.5 Started at 127.0.0.1:8080")
    
    # Create and init our Module class
    flex_module = Module()
    
    # Load the HMI from the database
    savedPlan = db.fetch_from_db("FlexLayout")
    if savedPlan is not None:
        FlexPlan = savedPlan
        load_modules(savedPlan)    
    
    # The Flask Webserver sits in its own runtime thread
    frontEnd = Thread(target=frontEnd)
    frontEnd.start()
    
    # An alternative to the backEnd thread above, using an accurate (~10mS) twisted timing. Still subject to system load fluctuations.
    backEnd = task.LoopingCall(flex_module.process)
    backEnd.start(1)
    reactor.run()


    
    
