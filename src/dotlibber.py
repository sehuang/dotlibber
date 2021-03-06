import os
import re
import json
import sys
from datetime import datetime

# Define how many spaces are in an indentation
IWIDTH = 4

def to_s(foo):
    return foo.__str__()

# Default function to name the .lib, passed to the Library constructor
def default_library_namer(lib, corner):
    return lib.name + "_" + corner.name

def default_file_namer(lib, corner):
    return os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "output", default_library_namer(lib, corner) + ".lib")

default_characterizer_global = 0.0
def default_characterizer(arc_type, timing_type, pin, related_pin, corner, params):
    global default_characterizer_global
    default_characterizer_global += 0.1
    return default_characterizer_global

class Corner:

    def __init__(self, attr, characterizer):
        self.attr = attr
        self.name = get_name(self)
        self.short_name = self.name
        if "short_name" in self.attr.keys():
            self.short_name = self.attr["short_name"]
        self.process = require_int(self, "process")
        self.temperature = require_int(self, "temperature")
        require_key(self, "voltage_map")
        self.characterizer = characterizer
        self.voltage = require_float(self, "nominal_voltage")
        self.voltage_map = {}
        for k in self.attr["voltage_map"]:
            self.voltage_map[k] = float(self.attr["voltage_map"][k])
        require_key(self, "constraint_template")
        require_key(self, "delay_template")
        try:
            x = self.attr["constraint_template"]
            self.constraint_template = LUTTemplate("constraint_template_%dx%d" % (len(x["related_pin_transition"]),len(x["constrained_pin_transition"])),
                "related_pin_transition",
                x["related_pin_transition"],
                "constrained_pin_transition",
                x["constrained_pin_transition"])
            x = self.attr["delay_template"]
            self.delay_template = LUTTemplate("delay_template_%dx%d" % (len(x["input_net_transition"]),len(x["total_output_net_capacitance"])),
                "input_net_transition",
                x["input_net_transition"],
                "total_output_net_capacitance",
                x["total_output_net_capacitance"])
        except:
            sys.stderr.write("Error reading LUT templates for corner \"%s\". Aborting.\n" % self.name)
            exit(1)

    def emit(self):
        output  = "nom_process : %d;\n" % self.process
        output += "nom_temperature : %d;\n" % self.temperature
        output += "nom_voltage : %f;\n" % self.voltage
        for k in self.voltage_map:
            output += "voltage_map(%s, %f);\n" % (k, self.voltage_map[k])
        output += "operating_conditions(\"%s\") {\n" % self.name
        output += indent("process : %d;\n") % self.process
        output += indent("temperature : %d;\n") % self.temperature
        output += indent("voltage : %f;\n") % self.voltage
        output += "}\n"
        output += "default_operating_conditions : %s;\n" % self.name
        output += self.constraint_template.emit()
        output += self.delay_template.emit()
        return output

class Library:

    def __init__(self, attr, corners, library_namer=default_library_namer, characterizer=default_characterizer, options=None):
        self.attr = attr
        self.name = get_name(self)
        self.datetime = datetime.now().strftime("%c")
        require_int(self,"revision")
        self.corners = []
        for c in corners:
            self.add_corner(c, characterizer)
        # Check that all corners have the same voltage names
        for c in self.corners:
            if (set(c.voltage_map.keys()) != set(self.voltage_names())):
                sys.stderr.write("Error: all corners must have the same voltage names! Aborting.\n")
                exit(1)
        require_key(self,"cells")
        self.cells = []
        self.library_namer = library_namer
        self.bus_types = {}
        for a in self.attr["cells"]:
            self.add_cell(a, self.bus_types)
        # Default configurations
        self.options={'delay_model': 'table_lookup',
                      'simulation': 'true',
                      'capacitive_load_unit': '(1, pf)',
                      'voltage_unit': '1V',
                      'current_unit': '1mA',
                      'time_unit': '1ns',
                      'pulling_resistance_unit': '1kohm'
                      }
        if options:
            self.options.update(options)

    def voltage_names(self):
        return self.corners[0].voltage_map.keys()

    def add_cell(self, cell_attr, bus_types):
        self.cells.append(Cell(self, cell_attr, bus_types))

    def add_corner(self, corner_attr, characterizer):
        self.corners.append(Corner(corner_attr, characterizer))

    def emit(self, corner):
        output  = "library (%s) {\n" % self.library_namer(self, corner)
        header  = "technology (cmos);\n"
        header += "date : \"%s\";\n" % self.datetime
        header += "comment : \"Generated by dotlibber.py\";\n"
        header += "revision : %s;\n" % self.attr["revision"]
        # Assert that we're only doing NLDM
        header += "delay_model : %s;\n" % self.options['delay_model']
        header += "simulation : %s;\n" % self.options['simulation']
        # Unit specifications
        header += "capacitive_load_unit %s;\n" % self.options['capacitive_load_unit']
        header += "voltage_unit : \"%s\";\n" % self.options['voltage_unit']
        header += "current_unit : \"%s\";\n" % self.options['current_unit']
        header += "time_unit : \"%s\";\n" % self.options['time_unit']
        header += "pulling_resistance_unit : \"%s\";\n" % self.options['pulling_resistance_unit']
        header += corner.emit()
        output += indent(header)
        output += "\n"
        output += "\n".join(list(map(lambda kv: kv[1], self.bus_types.items())))
        for c in self.cells:
            output += indent(c.emit(corner))
            output += "\n"

        output += "}\n"
        return output


    def write_all(self, file_namer=default_file_namer, file_dir=None):
        for corner in self.corners:
            if not(file_dir is None):
                f = os.path.join(file_dir, file_namer(self, corner))
            else:
                f = file_namer(self, corner)

            # this is basically mkdir -p
            try:
                os.makedirs(os.path.dirname(f))
            except OSError:
                pass
            open(f,"w").write(self.emit(corner))

class LUTTemplate:

    def __init__(self, name, var1, index_1, var2=None, index_2=[]):
        self.name = name
        self.var1 = var1
        self.var2 = var2
        self.index_1 = index_1
        self.index_2 = index_2
        self.twod = var2 is not None

    def emit(self):
        output  = "variable_1 : %s;\n" % self.var1
        if self.twod:
            output += "variable_2 : %s;\n" % self.var2
        output += "index_1 (\"%s\");\n" % ", ".join(map(to_s, self.index_1))
        if self.twod:
            output += "index_2 (\"%s\");\n" % ", ".join(map(to_s, self.index_2))
        return "lu_table_template (%s) {\n" % self.name + indent(output) + "}\n"

class Cell:

    def __init__(self, lib, attr, bus_types):
        self.lib = lib
        self.attr = attr
        self.name = get_name(self)
        require_key(self, "pg_pins")
        require_key(self, "pins")
        self.pg_pins = []
        self.pins = []
        self.clocks = {}
        self.sequential_pins = []
        self.defaults = {}
        if ("defaults" in self.attr.keys()):
            self.defaults = self.attr["defaults"]
        for p in self.attr["pg_pins"]:
            self.add_pg_pin(p)
        for p in self.attr["pins"]:
            ###matches bus defined with [upper:lower] and <upper:lower>
            m = re.match(r"^([\w_]+)[\[<](\d+):(\d+)[\]>]$", p["name"]) if "name" in p.keys() else False

            ####matches bus defined with [upper:lower]
            #m = re.match(r"^([\w_]+)\[(\d+):(\d+)]$", p["name"]) if "name" in p.keys() else False

            if m:
                # This is a bus
                a = int(m.group(2))
                b = int(m.group(3))
                lower = min(a,b)
                upper = max(a,b)
                base = m.group(1)
                p["name"] = base
                p["is_bus"] = True
                p["bus_max"] = upper
                p["bus_min"] = lower
                self.add_pin(p)
                bus_types[(upper, lower)] = """
    type (bus_%d_to_%d) {
        base_type : array ;
        data_type : bit ;
        bit_width : %d ;
        bit_from : %d ;
        bit_to : %d ;
        downto : true ;
    }\n""" % (upper, lower, upper-lower+1, upper, lower)

            elif p.get('is_bus', None):
                if p['is_bus'] == False:
                    pass
                upper = p["bus_max"]
                lower = p["bus_min"]
                self.add_pin(p)
                bus_types[(upper, lower)] = """
    type (bus_%d_to_%d) {
        base_type : array ;
        data_type : bit ;
        bit_width : %d ;
        bit_from : %d ;
        bit_to : %d ;
        downto : true ;
    }\n""" % (upper, lower, upper - lower + 1, upper, lower)
            else:
                self.add_pin(p)
        for p in self.sequential_pins:
            for c in self.lib.corners:
                p.generate_arcs(c)

    def power_pins(self):
        return filter(lambda x: x.type == "primary_power", self.pg_pins)

    def ground_pins(self):
        return filter(lambda x: x.type == "primary_ground", self.pg_pins)

    def add_pg_pin(self, pg_pin_attr):
        self.pg_pins.append(PGPin(self, pg_pin_attr))

    def add_pin(self, pin_attr):
        self.pins.append(Pin(self, pin_attr, self.defaults))

    def add_clock(self, pin):
        self.clocks[pin.name] = pin

    def get_clock(self, name):
        return self.clocks[name]

    def add_sequential_pin(self, pin):
        self.sequential_pins.append(pin)

    def emit(self, corner):
        output  = "cell (%s) {\n" % self.name
        # For now, always dont_touch, dont_use macros. We aren't using this for std cells.
        output += indent("dont_use : true;\n")
        output += indent("dont_touch : true;\n")
        output += indent("is_macro_cell : true;\n")
        for p in self.pg_pins + self.pins:
            output += "\n"
            output += indent(p.emit(corner))
        output += "}\n"
        return output

class Pin:

    def __init__(self, cell, attr, defaults):
        self.cell = cell
        self.attr = attr
        self.name = get_name(self)
        self.arcs = {}
        for c in self.cell.lib.corners:
            self.arcs[c] = []
        # Use a list here to enforce an ordering
        self.output_attr = []
        self.bus_attr = []
        # bus_type must be first
        self.is_bus = require_boolean(self, "is_bus", False)
        if(self.is_bus):
            self.bus_max = require_int(self, "bus_max", 0)
            self.bus_min = require_int(self, "bus_min", 0)
            self.output_attr.append(("bus_type", "bus_%d_to_%d" %(self.bus_max, self.bus_min)))
        self.output_attr.append(("direction", require_values(self, "direction", ["input", "output", "inout"])))
        self.direction = self.attr["direction"]
        self.is_analog = require_boolean(self, "is_analog", False)
        # Digital pin checks
        if not self.is_analog:
            self.clock = require_boolean(self, "clock", False)
            self.reset = require_boolean(self, "reset", False)
            if self.clock:
                self.cell.add_clock(self)
                self.sequential = False
                self.output_attr.append(("clock", "true"))
                if self.reset:
                    sys.stderr.write("Pin \"%s\" of cell \"%s\" cannot be both clock and reset. Aborting.\n" % (self.name, self.cell.name))
                    exit(1)
            else:
                self.sequential = require_boolean(self, "sequential", False)
                if self.sequential:
                    require_key(self, "related_clock")
                    self.related_clock_name = self.attr["related_clock"]
                    self.cell.add_sequential_pin(self)
                else:
                    self.related_clock_name = None

            if self.direction == "inout":
                sys.stderr.write("Digital inout pins are not supported. FIXME. Aborting\n")
                exit(1)

            if self.direction == "input":
                self.output_attr.append(("capacitance", require_float(self, "capacitance", defaults)))
                self.output_attr.append(("max_transition", require_float(self, "max_transition", defaults)))

            if self.direction == "output":
                self.output_attr.append(("max_capacitance", require_float(self, "max_capacitance", defaults)))

            # Assert that we must have a power pin with the name in our PG pin list
            related_power_pin = ("related_power_pin",require_values(self, "related_power_pin", map(lambda x: x.name, self.cell.power_pins()), defaults))
            # Assert that we must have a ground pin with the name in our PG pin list
            related_ground_pin = ("related_ground_pin",require_values(self, "related_ground_pin", map(lambda x: x.name, self.cell.ground_pins()), defaults))
            if self.is_bus:
                self.bus_attr.append(related_power_pin)
                self.bus_attr.append(related_ground_pin)
            else:
                self.output_attr.append(related_power_pin)
                self.output_attr.append(related_ground_pin)
        else:
            self.output_attr.append(("is_analog", "true"))

    def get_related_clock(self):
        if self.related_clock_name is not None:
            return self.cell.get_clock(self.related_clock_name)

    def has_attr(self, attr):
        return attr in self.attr.keys()

    def generate_arcs(self, corner):
        if self.related_clock_name not in self.cell.clocks.keys():
            sys.stderr.write("Related clock pin \"%s\" of pin \"%s\" of cell \"%s\" is not defined as a clock. Please give it the \"clock : true\" attribute. Aborting.\n" % (self.related_clock_name, self.name, self.cell.name))
            exit(1)
        else:
            if self.direction == "input":
                self.arcs[corner].append(SetupArc(self, self.get_related_clock(), corner))
                self.arcs[corner].append(HoldArc(self, self.get_related_clock(), corner))
            elif self.direction == "output":
                self.arcs[corner].append(ClockToQArc(self, self.get_related_clock(), corner))
            else:
                raise Exception("Should not get here, fix me. You have an inout sequential pin, or something else went wrong.")

    def emit(self, corner):
        attributes = "".join(map(lambda x: "%s : %s;\n" % (x[0], x[1]), self.output_attr))
        bus_attributes = "".join(map(lambda x: "%s : %s;\n" % (x[0], x[1]), self.bus_attr))
        # TODO some attributes are corner-specific (cap, max_cap, etc.) and need to be characterized
        for a in self.arcs[corner]:
            attributes += a.emit()
        if self.is_bus:
            output = "bus ( %s ) {\n" % self.name + indent(attributes) + "\n"
            output += indent("pin ( %s[%d:%d] ) {\n" % (self.name, self.bus_max, self.bus_min) + indent(bus_attributes) + "}\n") + "}\n"
        else:
            output = "pin (%s) {\n" % self.name + indent(attributes) + "}\n"
        return output

class SetupArc:

    def __init__(self, pin, related_pin, corner):
        self.pin = pin
        self.related_pin = related_pin
        self.rise_constraint = generate_data_table("rise_constraint", "setup_rising", pin, related_pin, corner.constraint_template, corner)
        self.fall_constraint = generate_data_table("fall_constraint", "setup_rising", pin, related_pin, corner.constraint_template, corner)

    def emit(self):
        output  = "related_pin : \"%s\";\n" % self.related_pin.name
        # Note that we only support rising clocks for now
        output += "timing_type : setup_rising;\n"
        output += self.rise_constraint.emit()
        output += self.fall_constraint.emit()
        return "timing () {\n" + indent(output) + "}\n"

class HoldArc:

    def __init__(self, pin, related_pin, corner):
        self.pin = pin
        self.related_pin = related_pin
        self.rise_constraint = generate_data_table("rise_constraint", "hold_rising", pin, related_pin, corner.constraint_template, corner)
        self.fall_constraint = generate_data_table("fall_constraint", "hold_rising", pin, related_pin, corner.constraint_template, corner)

    def emit(self):
        output  = "related_pin : \"%s\";\n" % self.related_pin.name
        # Note that we only support rising clocks for now
        output += "timing_type : hold_rising;\n"
        output += self.rise_constraint.emit()
        output += self.fall_constraint.emit()
        return "timing () {\n" + indent(output) + "}\n"

class ClockToQArc:

    def __init__(self, pin, related_pin, corner):
        self.pin = pin
        self.related_pin = related_pin
        self.cell_rise = generate_data_table("cell_rise", "rising_edge", pin, related_pin, corner.delay_template, corner)
        self.cell_fall = generate_data_table("cell_fall", "rising_edge", pin, related_pin, corner.delay_template, corner)
        self.rise_transition = generate_data_table("rise_transition", "rising_edge", pin, related_pin, corner.delay_template, corner)
        self.fall_transition = generate_data_table("fall_transition", "rising_edge", pin, related_pin, corner.delay_template, corner)

    def emit(self):
        output  = "related_pin : \"%s\";\n" % self.related_pin.name
        output += "timing_sense : non_unate;\n"
        # Note that we only support rising clocks for now
        output += "timing_type : rising_edge;\n"
        output += self.cell_rise.emit()
        output += self.rise_transition.emit()
        output += self.cell_fall.emit()
        output += self.fall_transition.emit()
        return "timing () {\n" + indent(output) + "}\n"

def generate_data_table(arc_type, timing_type, pin, related_pin, template, corner):
    len1 = len(template.index_1)
    len2 = len(template.index_2) if template.twod else 1
    data = [[None for i in range(len1)] for j in range(len2)]
    params = {}
    for x2 in range(len2):
        for x1 in range(len1):
            params[template.var1] = template.index_1[x1]
            if template.twod:
                params[template.var2] = template.index_2[x2]
            data[x2][x1] = corner.characterizer(arc_type, timing_type, pin, related_pin, corner, params)
    return DataTable(arc_type, template, data)

class DataTable:

    def __init__(self, name, template, data):
        self.name = name
        self.template = template
        self.index_1 = template.index_1
        self.index_2 = template.index_2
        self.twod = template.twod
        # Sanity check
        for x in self.index_1 + self.index_2:
            if type(x) != type(0.0):
                raise
        if self.twod:
            self.data = data
            # Sanity check dimensions and floats
            if len(self.data) != len(self.index_2):
                raise
            for x2 in range(len(self.index_2)):
                if len(self.data[x2]) != len(self.index_2):
                    raise
                for x1 in range(len(self.index_1)):
                    if type(data[x2][x1]) != type(0.0):
                        raise
        else:
            self.data = data
            # Sanity check dimensions and floats
            if len(self.data[0]) != len(self.index_1):
                raise
            if len(self.data) != 1:
                raise
            for x1 in range(len(self.index_1)):
                if type(data[0][x1]) != type(0.0):
                    raise

    def emit(self):
        output  = "%s (%s) {\n" % (self.name, self.template.name)
        output += indent("index_1 (\"%s\");\n" % ", ".join(map(to_s, self.index_1)))
        if self.twod:
            output += indent("index_2 (\"%s\");\n" % ", ".join(map(to_s, self.index_2)))
        output += indent("values ( \\\n")
        output += indent(", \\\n".join(map(lambda y: "\"" + ", ".join(map(to_s, y)) + "\"", self.data)) + " \\\n",2)
        output += indent(");\n")
        output += "}\n"
        return output

class PGPin:

    def __init__(self, cell, attr):
        self.cell = cell
        self.attr = attr
        self.name = get_name(self)
        # For now only implement primary power/ground. If anyone needs secondary power/ground, you get to update this!
        require_values(self, "pg_type", ["primary_power", "primary_ground"])
        require_values(self, "name", self.cell.lib.voltage_names())
        self.type = self.attr["pg_type"]

    def emit(self, corner):
        output = "pg_pin (%s) {\n" % self.name
        output += indent("pg_type : %s;\n" % self.type)
        # For now assert that voltage_name is the same as the pin name
        output += indent("voltage_name : %s;\n" % self.name)
        output += "}\n"
        return output

def get_name(obj):
    if "name" not in obj.attr.keys():
        sys.stderr.write("Missing name for %s object. Aborting.\n" % obj.__class__.__name__)
        exit(1)
    return obj.attr["name"]

def require_key(obj, key):
    if key not in obj.attr.keys():
        sys.stderr.write("Missing required key \"%s\" for %s object %s. Aborting.\n" % (key, obj.__class__.__name__, obj.name))
        exit(1)

def require_key_or_default(obj, key, default=None):
    if default is not None:
        if type(default) is dict:
            if key in default.keys():
                obj.attr[key] = default[key]
        elif key not in obj.attr.keys():
            obj.attr[key] = default
    require_key(obj, key)

def require_boolean(obj, key, default=None):
    if key in obj.attr:
        if type(obj.attr[key]) != type(False):
            sys.stderr.write("Invalid entry \"%s\" for attribute \"%s\" of %s object %s. Must be true or false. Aborting.\n" % (obj.attr[key].__str__(), key, obj.__class__.__name__, obj.name))
            exit(1)
        return obj.attr[key]
    else:
        if default is not None:
            return default
        else:
            require_key(obj, key)

def require_values(obj, key, values, default=None):
    require_key_or_default(obj, key, default)
    if obj.attr[key] not in values:
        sys.stderr.write("Invalid entry \"%s\" for attribute \"%s\" of %s object %s. Allowed values are %s. Aborting.\n" % (obj.attr[key].__str__(), key, obj.__class__.__name__, obj.name, ', '.join(values)))
        exit(1)
    return obj.attr[key]

def optional_values(obj, key, values, default=None):
    if key in obj.attr:
        require_values(obj, key, values)
        return obj.attr[key]
    else:
        return default

def require_float(obj, key, default=None):
    require_key_or_default(obj, key, default)
    if (type(obj.attr[key]) != type(0.0)):
        sys.stderr.write("Invalid entry \"%s\" for attribute \"%s\" of %s object %s. Must be a float. Aborting.\n" % (obj.attr[key], key, obj.__class__.__name__, obj.name))
        exit(1)
    return obj.attr[key]

def require_int(obj, key, default=None):
    require_key_or_default(obj, key, default)
    if (type(obj.attr[key]) != type(0)):
        sys.stderr.write("Invalid entry \"%s\" for attribute \"%s\" of %s object %s. Must be a int. Aborting.\n" % (obj.attr[key], key, obj.__class__.__name__, obj.name))
        exit(1)
    return obj.attr[key]

def indent(s, lvl=1):
    return re.compile('^([^\n])',re.MULTILINE).sub(" " * IWIDTH * lvl + "\\1", s)

def read_library_json(libfile, cornerfile, library_namer=default_library_namer, characterizer=default_characterizer):
    try:
        lib_attr = json.load(open(libfile))
    except:
        sys.stderr.write("Syntax error parsing JSON file %s. Aborting.\n" % libfile)
        exit(1)
    try:
        corner_attr = json.load(open(cornerfile))["corners"]
    except:
        sys.stderr.write("Syntax error parsing JSON file %s. Aborting.\n" % cornerfile)
        exit(1)
    return Library(lib_attr, corner_attr, library_namer, characterizer)
