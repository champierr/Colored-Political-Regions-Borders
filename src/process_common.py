import string
import types

def convert_to_identifier(s0):
  s1 = str.replace(s0," ","_")
  s2 = str.replace(s1,"'","_")
  s3 = str.replace(s2,"`","_")
  s4 = str.replace(s3,"(","_")
  s5 = str.replace(s4,")","_")
  s6 = str.replace(s5,"-","_")
  s7 = str.replace(s6,",","")
  s8 = str.replace(s7,"|","")
  s9 = str.replace(s8,"\t","_") #Tab
  s10 = str.lower(s9)
  return s10

def convert_to_identifier_with_no_lowercase(s0):
  s1 = str.replace(s0," ","_")
  s2 = str.replace(s1,"'","_")
  s3 = str.replace(s2,"`","_")
  s4 = str.replace(s3,"(","_")
  s5 = str.replace(s4,")","_")
  s6 = str.replace(s5,"-","_")
  s7 = str.replace(s6,",","")
  s8 = str.replace(s7,"|","")
  s9 = str.replace(s8,"\t","_") #Tab
  return s9

def replace_spaces(s0):
  return str.replace(str.replace(s0,"\t","_")," ","_")
