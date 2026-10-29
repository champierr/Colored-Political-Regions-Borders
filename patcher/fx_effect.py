"""Reads and writes compiled Direct3D 9 effects (fx_2_0, the format of Warband's mb_2a.fxo / mb.fx).

Only what CPR&B needs: list parameters and techniques, swap the shader bytecode of a pass, and append new
parameters. The layout follows Wine's d3dx9 effect loader (dlls/d3dx9_36/effect.c):

  u32 tag (0xFEFF0901), u32 size of the data block, data block, then the structure:
  u32 parameter count, technique count, shader slot count, object count
  parameters: typedef offset, value offset, flags, annotation count, annotations (typedef, value offsets)
  techniques: name offset, annotation count, pass count, annotations,
              passes: name offset, annotation count, state count, annotations,
                      states: operation, index, typedef offset, value offset
  u32 string count, resource count
  strings:   object id, size, bytes (padded to 4)
  resources: technique index, pass index, element index, state index, usage, [size, bytes (padded)]
Offsets point into the data block. Typedefs start with type, class, name offset, semantic offset.
"""
import struct

TAG = 0xFEFF0901

# D3DXPARAMETER_TYPE / D3DXPARAMETER_CLASS
PT_BOOL, PT_INT, PT_FLOAT, PT_STRING = 1, 2, 3, 4
PT_PIXELSHADER, PT_VERTEXSHADER = 15, 16
PC_SCALAR, PC_VECTOR, PC_MATRIX_ROWS, PC_MATRIX_COLUMNS = 0, 1, 2, 3


class FxError(Exception):
  pass


def _pad(n):
  return (n + 3) & ~3


class Reader:
  def __init__(self, data, pos):
    self.data, self.pos = data, pos

  def u32(self):
    v = struct.unpack_from("<I", self.data, self.pos)[0]
    self.pos += 4
    return v

  def blob(self):
    """Bytes with their original padding (the compiler leaves junk there; keep it for exact output)."""
    size = self.u32()
    b = Blob(self.data[self.pos:self.pos + size], self.data[self.pos + size:self.pos + _pad(size)])
    self.pos += _pad(size)
    return b


class Blob(bytes):
  def __new__(cls, data, padding=None):
    blob = super().__new__(cls, data)
    blob.padding = padding if padding is not None else b"\0" * (_pad(len(data)) - len(data))
    return blob


class Effect:
  def __init__(self, raw):
    tag, data_size = struct.unpack_from("<II", raw, 0)
    if tag != TAG:
      raise FxError("not a compiled fx_2_0 effect")
    self.data = bytearray(raw[8:8 + data_size])
    r = Reader(raw, 8 + data_size)
    self.header = [r.u32() for _ in range(4)]          # params, techniques, shader slots, objects
    n_params, n_techniques = self.header[0], self.header[1]
    self.params = []                                   # [typedef, value, flags, [annotations]]
    for _ in range(n_params):
      p = [r.u32(), r.u32(), r.u32()]
      n_anno = r.u32()
      p.append([(r.u32(), r.u32()) for _ in range(n_anno)])
      self.params.append(p)
    self.techniques = []                               # [name, [annotations], [passes]]
    for _ in range(n_techniques):
      name = r.u32(); n_anno = r.u32(); n_pass = r.u32()
      annos = [(r.u32(), r.u32()) for _ in range(n_anno)]
      passes = []                                      # [name, [annotations], [states]]
      for _ in range(n_pass):
        pname = r.u32(); pn_anno = r.u32(); n_state = r.u32()
        pannos = [(r.u32(), r.u32()) for _ in range(pn_anno)]
        states = [[r.u32(), r.u32(), r.u32(), r.u32()] for _ in range(n_state)]
        passes.append([pname, pannos, states])
      self.techniques.append([name, annos, passes])
    n_strings = r.u32(); n_resources = r.u32()
    self.strings = [(r.u32(), r.blob()) for _ in range(n_strings)]
    self.resources = []                                # [technique, pass, element, state, usage, data or None]
    for _ in range(n_resources):
      res = [r.u32() for _ in range(5)]
      res.append(r.blob() if self._resource_has_data(res) else None)
      self.resources.append(res)
    if r.pos != len(raw):
      raise FxError("%d unparsed bytes at the end" % (len(raw) - r.pos))

  # ------------------------------------------------------------------------------------- data block
  def _u32(self, offset):
    return struct.unpack_from("<I", self.data, offset)[0]

  def name_at(self, offset):
    size = self._u32(offset)
    return self.data[offset + 4:offset + 4 + size].rstrip(b"\0").decode("latin-1") if size else ""

  def typedef(self, offset):
    """(type, class, name) of a typedef in the data block."""
    return self._u32(offset), self._u32(offset + 4), self.name_at(self._u32(offset + 8))

  def _resource_has_data(self, res):
    technique, pass_index, element, state, usage = res
    if usage in (1, 2):
      return True
    if usage != 0:
      return False
    if technique == 0xFFFFFFFF:
      raise FxError("sampler state resources are not supported")
    typedef = self.techniques[technique][2][pass_index][2][state][2]
    return self._u32(typedef) in (PT_VERTEXSHADER, PT_PIXELSHADER, PT_BOOL, PT_INT, PT_FLOAT, PT_STRING)

  # ------------------------------------------------------------------------------------------ queries
  def param_names(self):
    return [self.typedef(p[0])[2] for p in self.params]

  def technique_names(self):
    return [self.name_at(t[0]) for t in self.techniques]

  def shader_resources(self, technique_index):
    """{(pass index, 'vs'|'ps'): resource index} of the compiled shaders set by a technique."""
    result = {}
    for i, (t, p, element, s, usage, data) in enumerate(self.resources):
      if t == technique_index and usage == 0 and data is not None:
        kind = self._u32(self.techniques[t][2][p][2][s][2])
        if kind in (PT_VERTEXSHADER, PT_PIXELSHADER):
          result[(p, "vs" if kind == PT_VERTEXSHADER else "ps")] = i
    return result

  def param_definition(self, index):
    """Everything needed to recreate a simple (scalar/vector/matrix, non-array) parameter elsewhere."""
    typedef, value, flags, annos = self.params[index]
    ptype, pclass, name = self.typedef(typedef)
    if pclass not in (PC_SCALAR, PC_VECTOR, PC_MATRIX_ROWS, PC_MATRIX_COLUMNS) or self._u32(typedef + 16) or annos:
      raise FxError("parameter %s is not a simple numeric parameter" % name)
    dims = (self._u32(typedef + 20), self._u32(typedef + 24))
    size = 4 * dims[0] * dims[1]
    return {"name": name, "type": ptype, "class": pclass, "dims": dims, "flags": flags,
            "value": bytes(self.data[value:value + size])}

  # ---------------------------------------------------------------------------------------- changes
  def _append_data(self, b):
    offset = len(self.data)
    self.data += b + b"\0" * (_pad(len(b)) - len(b))
    return offset

  def add_param(self, definition):
    name = definition["name"].encode("latin-1") + b"\0"
    name_offset = self._append_data(struct.pack("<I", len(name)) + name)
    semantic_offset = self._append_data(struct.pack("<I", 0))
    typedef = self._append_data(struct.pack("<7I", definition["type"], definition["class"], name_offset,
                                            semantic_offset, 0, *definition["dims"]))
    value = self._append_data(definition["value"])
    self.params.append([typedef, value, definition["flags"], []])

  def set_resource_data(self, index, data):
    self.resources[index][5] = Blob(data)

  def to_bytes(self):
    out = [struct.pack("<II", TAG, len(self.data)), bytes(self.data)]
    header = list(self.header)
    header[0], header[1] = len(self.params), len(self.techniques)
    out.append(struct.pack("<4I", *header))
    for typedef, value, flags, annos in self.params:
      out.append(struct.pack("<4I", typedef, value, flags, len(annos)))
      out += [struct.pack("<2I", *a) for a in annos]
    for name, annos, passes in self.techniques:
      out.append(struct.pack("<3I", name, len(annos), len(passes)))
      out += [struct.pack("<2I", *a) for a in annos]
      for pname, pannos, states in passes:
        out.append(struct.pack("<3I", pname, len(pannos), len(states)))
        out += [struct.pack("<2I", *a) for a in pannos]
        out += [struct.pack("<4I", *st) for st in states]
    out.append(struct.pack("<2I", len(self.strings), len(self.resources)))
    for object_id, blob in self.strings:
      out.append(struct.pack("<II", object_id, len(blob)) + blob + blob.padding)
    for *res, data in self.resources:
      out.append(struct.pack("<5I", *res))
      if data is not None:
        out.append(struct.pack("<I", len(data)) + data + data.padding)
    return b"".join(out)


def load(path):
  with open(path, "rb") as f:
    return Effect(f.read())
