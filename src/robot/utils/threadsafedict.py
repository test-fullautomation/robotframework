from threading import RLock


class ThreadSafeDict(dict):
   def __init__(self):
      super(ThreadSafeDict, self).__init__()
      self.__RLock = RLock()

   def __setitem__(self, key, item):
      with self.__RLock:
         super(ThreadSafeDict, self).__setitem__(key, item)

   def __delitem__(self, key):
      with self.__RLock:
         super(ThreadSafeDict, self).__delitem__(key)

   def clear(self):
      with self.__RLock:
         super(ThreadSafeDict, self).clear()

   def pop(self, key, *args):
      with self.__RLock:
         o = super(ThreadSafeDict, self).pop(key, *args)
      return o

   def popitem(self):
      with self.__RLock:
         o = super(ThreadSafeDict, self).popitem()
      return o

   def update(self, dict=None):
      if dict is None:
         pass
      elif isinstance(dict, type({})):
         for (key, value) in dict.items():
            self.__setitem__(key, value)
      else:
         raise TypeError
