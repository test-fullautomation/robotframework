import queue


class QueuedNotification:
   def __init__(self, name=None):
      self.params = {}
      self.name = name

   def __del__(self):
      pass


class PriorityQueue(queue.PriorityQueue, object):
   def __init__(self, queue_type="FIFO", callback=None):
      super(PriorityQueue, self).__init__()
      self.counter = 0
      self._callback = callback
      self.factor = -1
      if queue_type == "LIFO":
         self.factor = -1
      elif queue_type == "FIFO":
         self.factor = 1
      else:
         raise Exception("Fatal Error: PriorityQueue type not allowed: '%s'!" % str(queue_type))

   def set_callback(self, callback):
      """Set the callback function."""
      self._callback = callback

   def _trigger_callback(self, action, *args):
      """Call the callback function with the action and item."""
      # from robot.libraries.BuiltIn import BuiltIn
      if self._callback:
         # BuiltIn().log_to_console(f"callback in {id(self)}")
         self._callback(action, *args)

   def put(self, item, priority=None, skip_callback=False):
      # from robot.libraries.BuiltIn import BuiltIn
      if priority is None:
         self.counter += 1
         priority = self.counter

      super(PriorityQueue, self).put((self.factor * priority, item), block=True)
      if not skip_callback:
         # BuiltIn().log_to_console(f"put item {item.name} into {id(self)}")
         self._trigger_callback("put", item)

   def get(self, *args, **kwargs):
      (priority, item) = super(PriorityQueue, self).get(*args, **kwargs)
      return priority, item
