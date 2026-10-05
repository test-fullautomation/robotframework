# **************************************************************************************************************
#
#  Minimal drop-in fallback for the two PythonExtensionsCollection classes that
#  CComparison uses, so the log_level acceptance suites run even when that
#  package is not installed (e.g. on CI runners without access to it). The real
#  package is preferred; this module is imported only when it is unavailable.
#
#  Only the subset used by CComparison is implemented, matching the behaviour of
#  CFile.ReadLines / CString.NormalizePath / CString.FormatResult. ReadLines is
#  applied symmetrically to the generated file and the reference file, so the
#  comparison result is independent of small formatting choices here.
#
# **************************************************************************************************************

import os


class CString():

   @staticmethod
   def NormalizePath(sPath=None, bWin=False, sReferencePathAbs=None,
                     bConsiderBlanks=False, bExpandEnvVars=True, bMask=True):
      if sPath is None:
         return None
      sPath = os.path.abspath(os.path.normpath(str(sPath)))
      return sPath.replace(os.sep, '/')

   @staticmethod
   def FormatResult(sMethod="", bSuccess=True, sResult=""):
      return sResult


class CFile():

   def __init__(self, sFile=None):
      self.__sFile = sFile

   def ReadLines(self, bCaseSensitive=True, bSkipBlankLines=False, sComment=None,
                 sStartsWith=None, sEndsWith=None, sStartsNotWith=None,
                 sEndsNotWith=None, sContains=None, sContainsNot=None,
                 sInclRegEx=None, sExclRegEx=None, bLStrip=False, bRStrip=True,
                 bToScreen=False):
      listLines = []
      if not self.__sFile or os.path.isfile(self.__sFile) is False:
         return listLines, False, f"The file '{self.__sFile}' does not exist."
      try:
         with open(self.__sFile, "r", encoding="utf-8") as oFileHandle:
            sFileContent = oFileHandle.read()
      except Exception as reason:
         return listLines, None, (f"Not possible to read from file "
                                  f"'{self.__sFile}'.\nReason: {reason}")
      # sContainsNot is a ';'-separated set of substrings; a line is excluded
      # if it contains any of them. CComparison only passes None here, but the
      # filter is kept so the fallback stays faithful.
      excludes = sContainsNot.split(";") if sContainsNot else []
      for sLine in sFileContent.splitlines():   # OS independent, like the original
         if bSkipBlankLines is True and sLine.strip() == "":
            continue
         if excludes and any(sExcl in sLine for sExcl in excludes):
            continue
         if bLStrip is True:
            sLine = sLine.lstrip(" \t\r\n")
         if bRStrip is True:
            sLine = sLine.rstrip(" \t\r\n")
         if bToScreen is True:
            print(sLine)
         listLines.append(sLine)
      return listLines, True, ""
