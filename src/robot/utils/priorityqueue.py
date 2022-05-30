import queue


class PriorityQueue(queue.PriorityQueue, object):
   def __init__(self, queue_type="FIFO"):
      super(PriorityQueue, self).__init__()
      self.counter = 0

      self.factor = -1
      if queue_type == "LIFO":
         self.factor = -1
      elif queue_type == "FIFO":
         self.factor = 1
      else:
         raise Exception("Fatal Error: PriorityQueue type not allowed: '%s'!" % str(queue_type))

   def put(self, item, priority=None):
      if priority is None:
         self.counter += 1
         priority = self.counter

      super(PriorityQueue, self).put((self.factor * priority, item), block=True)

   def get(self, *args, **kwargs):
      (priority, item) = super(PriorityQueue, self).get(*args, **kwargs)
      return priority, item
