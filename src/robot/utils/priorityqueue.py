import heapq
import queue


class QueuedNotification:
   def __init__(self, name=None):
      self.params = {}
      self.name = name

   def __del__(self):
      pass


class PriorityQueue(queue.PriorityQueue, object):
   # cuongnht memory cap: notifications put into a queue that nobody ever
   # consumes (e.g. broadcasts into MainThread's queue) would otherwise grow
   # without bound during days-long runs. When the cap is reached the entry
   # that was put first is dropped, for LIFO as well as FIFO queues, and
   # counted in `dropped`.
   max_items = 10000

   def __init__(self, queue_type="FIFO", callback=None):
      super(PriorityQueue, self).__init__()
      self.counter = 0
      self.dropped = 0
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

      while self.max_items and self.qsize() >= self.max_items:
         if not self._drop_oldest():
            break
         self.dropped += 1
      super(PriorityQueue, self).put((self.factor * priority, item), block=True)
      if not skip_callback:
         # BuiltIn().log_to_console(f"put item {item.name} into {id(self)}")
         self._trigger_callback("put", item)

   def _drop_oldest(self):
      # Drop the entry that was put first. For FIFO queues that is the heap
      # root, but for LIFO queues priorities are negated, so `get()` would
      # return the newest entry and the oldest is the largest heap value.
      with self.mutex:
         if not self._qsize():
            return False
         if self.factor == 1:
            heapq.heappop(self.queue)
         else:
            index = max(range(len(self.queue)), key=lambda i: self.queue[i][0])
            self.queue[index] = self.queue[-1]
            del self.queue[-1]
            heapq.heapify(self.queue)
         self.not_full.notify()
         return True

   def get(self, *args, **kwargs):
      (priority, item) = super(PriorityQueue, self).get(*args, **kwargs)
      return priority, item
