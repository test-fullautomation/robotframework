*** Test Cases ***
Simple thread
   Log    Not yet in FOR
   THREAD    TEST    False
       SLEEP  2
       LOG TO CONSOLE   threading
   END
   Log    Not in FOR anymore