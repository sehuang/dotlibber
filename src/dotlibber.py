import os
import re
import json
import sys
from datetime import datetime

# Define how many spaces are in an indentation
IWIDTH = 4

# Default function to name the .lib, passed to the Library constructor
def default_library_namer(lib, corner):
    return lib.name + "_" + corner.name

def default_file_namer(lib, corner):
    return os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "output", default_library_namer(lib, corner) + ".lib")

class Corner:

    def __init__(self, attr):
        self.attr = attr
        self.name = get_name(self)
        self.process = require_int(self, "process")
        self.temperature = require_int(self, "temperature")
        require_key(self, "voltage_map")
        self.voltage = require_float(self, "nominal_voltage")
        self.voltage_map = {}
        for k in self.attr["voltage_map"]:
            self.voltage_map[k] = float(self.attr["voltage_map"][k])

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
        return output

class Library:

    def __init__(self, attr, corners, library_namer=default_library_namer):
        self.attr = attr
        self.name = get_name(self)
        self.datetime = datetime.now().strftime("%c")
        require_int(self,"revision")
        self.corners = []
        for c in corners:
            self.add_corner(c)
        # Check that all corners have the same voltage names
        for c in self.corners:
            if (set(c.voltage_map.keys()) != set(self.voltage_names())):
                sys.stderr.write("Error: all corners must have the same voltage names! Aborting.\n")
                exit(1)
        require_key(self,"cells")
        self.cells = []
        self.library_namer = library_namer
        for a in self.attr["cells"]:
            self.add_cell(a)

    def voltage_names(self):
        return self.corners[0].voltage_map.keys()

    def add_cell(self, cell_attr):
        self.cells.append(Cell(self, cell_attr))

    def add_corner(self, corner_attr):
        self.corners.append(Corner(corner_attr))

    def emit(self, corner):
        output  = "library (%s) {\n" % self.library_namer(self, corner)
        header  = "technology (cmos);\n"
        header += "date : \"%s\";\n" % self.datetime
        header += "comment : \"Generated by dotlibber.py\";\n"
        header += "revision : %s;\n" % self.attr["revision"]
        # Assert that we're only doing NLDM
        header += "delay_model : table_lookup;\n"
        header += "simulation : true;\n"
        # Here are a bunch of unit specs that nobody ever changes.
        header += "capacitive_load_unit : \"1pF\";\n"
        header += "voltage_unit : \"1V\";\n"
        header += "current_unit : \"1mA\";\n"
        header += "time_unit : \"1ns\";\n"
        header += "pulling_resistance_unit : \"1kohm\";\n"
        header += corner.emit()
        output += indent(header)
        output += "\n"
        for c in self.cells:
            output += indent(c.emit())
            output += "\n"

        output += "}\n"
        return output

    def write_all(self, file_namer=default_file_namer):
        for corner in self.corners:
            f = file_namer(self, corner)
            # this is basically mkdir -p
            try:
                os.makedirs(os.path.dirname(f))
            except OSError:
                pass
            open(f,"w").write(self.emit(corner))

class Cell:

    def __init__(self, lib, attr):
        self.lib = lib
        self.attr = attr
        self.name = get_name(self)
        require_key(self, "pg_pins")
        require_key(self, "pins")
        self.pg_pins = []
        self.pins = []
        self.clocks = {}
        self.sequential_pins = []
        for p in self.attr["pg_pins"]:
            self.add_pg_pin(p)
        for p in self.attr["pins"]:
            self.add_pin(p)
        for p in self.sequential_pins:
            p.generate_arcs()

    def power_pins(self):
        return filter(lambda x: x.type == "primary_power", self.pg_pins)

    def ground_pins(self):
        return filter(lambda x: x.type == "primary_ground", self.pg_pins)

    def add_pg_pin(self, pg_pin_attr):
        self.pg_pins.append(PGPin(self, pg_pin_attr))

    def add_pin(self, pin_attr):
        self.pins.append(Pin(self, pin_attr))

    def add_clock(self, pin):
        self.clocks[pin.name] = pin

    def get_clock(self, name):
        return self.clocks[name]

    def add_sequential_pin(self, pin):
        self.sequential_pins.append(pin)

    def emit(self):
        output  = "cell (%s) {\n" % self.name
        # For now, always dont_touch, dont_use macros. We aren't using this for std cells.
        output += indent("dont_use : true;\n")
        output += indent("dont_touch : true;\n")
        output += indent("is_macro_cell : true;\n")
        for p in self.pg_pins + self.pins:
            output += "\n"
            output += indent(p.emit())
        output += "}\n"
        return output

class Pin:

    def __init__(self, cell, attr):
        self.cell = cell
        self.attr = attr
        self.name = get_name(self)
        self.arcs = []
        # Use a list here to enforce an ordering
        self.output_attr = []
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
                self.output_attr.append(("capacitance", require_float(self, "capacitance")))
                self.output_attr.append(("max_transition", require_float(self, "max_transition")))

            if self.direction == "output":
                self.output_attr.append(("max_capacitance", require_float(self, "max_capacitance")))

            # Assert that we must have a power pin with the name in our PG pin list
            self.output_attr.append(("related_power_pin",require_values(self, "related_power_pin", map(lambda x: x.name, self.cell.power_pins()))))
            # Assert that we must have a ground pin with the name in our PG pin list
            self.output_attr.append(("related_ground_pin",require_values(self, "related_ground_pin", map(lambda x: x.name, self.cell.ground_pins()))))
        else:
            self.output_attr.append(("is_analog", "true"))

    def get_related_clock(self):
        if self.related_clock_name is not None:
            return self.cell.get_clock(self.related_clock_name)

    def has_attr(self, attr):
        return attr in self.attr.keys()

    def generate_arcs(self):
        if self.related_clock_name not in self.cell.clocks.keys():
            sys.stderr.write("Related clock pin \"%s\" of pin \"%s\" of cell \"%s\" is not defined as a clock. Please give it the \"clock : true\" attribute. Aborting.\n" % (self.related_clock_name, self.name, self.cell.name))
            exit(1)
        else:
            if self.direction == "input":
                self.arcs.append(SetupArc(self, self.get_related_clock()))
                self.arcs.append(HoldArc(self, self.get_related_clock()))
            elif self.direction == "output":
                self.arcs.append(ClockToQArc(self, self.get_related_clock()))
            else:
                raise Exception("Should not get here, fix me. You have an inout sequential pin, or something else went wrong.")

    # TODO emit timing arcs
    def emit(self):
        attributes = "".join(map(lambda x: "%s : %s;\n" % (x[0], x[1]), self.output_attr))
        for a in self.arcs:
            attributes += a.emit()
        return "pin (%s) {\n" % self.name + indent(attributes) + "}\n"

class SetupArc:

    def __init__(self, pin, related_pin):
        self.pin = pin
        self.related_pin = related_pin
        self.rise_constraint = DataTable("rise_constraint", self)
        self.fall_constraint = DataTable("fall_constraint", self)

    def emit(self):
        output  = "related_pin : \"%s\";\n" % self.related_pin.name
        # Note that we only support rising clocks for now
        output += "timing_type : setup_rising;\n"
        output += self.rise_constraint.emit()
        output += self.fall_constraint.emit()
        return "timing () {\n" + indent(output) + "}\n"

class HoldArc:

    def __init__(self, pin, related_pin):
        self.pin = pin
        self.related_pin = related_pin
        self.rise_constraint = DataTable("rise_constraint", self)
        self.fall_constraint = DataTable("fall_constraint", self)

    def emit(self):
        output  = "related_pin : \"%s\";\n" % self.related_pin.name
        # Note that we only support rising clocks for now
        output += "timing_type : hold_rising;\n"
        output += self.rise_constraint.emit()
        output += self.fall_constraint.emit()
        return "timing () {\n" + indent(output) + "}\n"

class ClockToQArc:

    def __init__(self, pin, related_pin):
        self.pin = pin
        self.related_pin = related_pin
        self.cell_rise = DataTable("cell_rise", self)
        self.cell_fall = DataTable("cell_fall", self)
        self.rise_transition = DataTable("rise_transition", self)
        self.fall_transition = DataTable("fall_transition", self)

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


class DataTable:

    def __init__(self, name, pin):
        self.name = name
        self.template_name = "template_3x3"
        self.index_1 = [0.01, 0.02, 0.03]
        self.index_2 = [0.01, 0.02, 0.03]
        self.data = [[0.01, 0.02, 0.03,],[0.04,0.05,0.06],[0.07,0.08,0.09]]

    def emit(self):
        output  = "%s (%s) {\n" % (self.name, self.template_name)
        output += indent("index_1 (\"%s\");\n" % ", ".join(map(lambda x: x.__str__(), self.index_1)))
        output += indent("index_2 (\"%s\");\n" % ", ".join(map(lambda x: x.__str__(), self.index_2)))
        output += indent("values ( \\\n")
        output += indent(", \\\n".join(map(lambda y: "\"" + ", ".join(map(lambda x: x.__str__(), y)) + "\"", self.data)) + " \\\n",2)
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

    def emit(self):
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

def require_values(obj, key, values):
    require_key(obj, key)
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

def require_float(obj, key):
    require_key(obj, key)
    if (type(obj.attr[key]) != type(0.0)):
        sys.stderr.write("Invalid entry \"%s\" for attribute \"%s\" of %s object %s. Must be a float. Aborting.\n" % (obj.attr[key], key, obj.__class__.__name__, obj.name))
        exit(1)
    return obj.attr[key]

def require_int(obj, key):
    require_key(obj, key)
    if (type(obj.attr[key]) != type(0)):
        sys.stderr.write("Invalid entry \"%s\" for attribute \"%s\" of %s object %s. Must be a int. Aborting.\n" % (obj.attr[key], key, obj.__class__.__name__, obj.name))
        exit(1)
    return obj.attr[key]

def indent(s, lvl=1):
    return re.compile('^([^\n])',re.MULTILINE).sub(" " * IWIDTH * lvl + "\\1", s)

def read_library_json(libfile, cornerfile, library_namer=default_library_namer):
    try:
        lib_attr = json.load(file(libfile))
    except:
        sys.stderr.write("Syntax error parsing JSON file %s. Aborting.\n" % libfile)
        exit(1)
    try:
        corner_attr = json.load(file(cornerfile))["corners"]
    except:
        sys.stderr.write("Syntax error parsing JSON file %s. Aborting.\n" % cornerfile)
        exit(1)
    return Library(lib_attr, corner_attr, library_namer)
