"""Compiles Module System style operation lists into Warband's text format, for mods we only have
compiled files of.

Operands: ints are written as they are (registers, positions, constants and already tagged ids);
":name" is a local variable; any other string is looked up with the resolver given to compile_block
("@text" quick strings, "script_...", "prsnt_...", "mesh_...", "trp_...").
"""
from warband.header_common import op_num_value_bits, tag_local_variable


def compile_block(ops, resolve):
  """' <count> <opcode> <operand count> <operands> ...' like process_operations.save_statement_block."""
  local_vars = {}
  parts = [str(len(ops))]
  for op in ops:
    if isinstance(op, int):                     # (try_begin) and friends are bare opcodes
      op = (op,)
    operands = []
    for value in op[1:]:
      if isinstance(value, int):
        operands.append(value)
      elif value.startswith(":"):
        index = local_vars.setdefault(value, len(local_vars))
        operands.append((tag_local_variable << op_num_value_bits) | index)
      else:
        operands.append(resolve(value))
    parts += [str(op[0]), str(len(operands))] + [str(v) for v in operands]
  return " " + " ".join(parts) + " "


def quick_string_line(text):
  """Line of quick_strings.txt for text, which the game shows with "_" turned back into spaces."""
  ident = "".join(c if c.isalnum() else "_" for c in text.lower())[:40]
  return "qstr_%s %s" % (ident, text.replace(" ", "_").replace("\t", "_"))
