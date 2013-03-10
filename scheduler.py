"""Utilities to schedule and manage periodic events.

This module depends on dateutil, which is often not included in the base python
packages installed in your system. Read here:

  http://labix.org/python-dateutil

for more details. Note that this library is extremely common. On Debian based
systems (including Ubuntu) you can install it with:

  apt-get install python-dateutil
"""

from dateutil import rrule # apt-get install python-dateutil
from dateutil import relativedelta
from dateutil import tz

import datetime
import time
import threading
import logging

class RecurringEvent(object):
  """Creates a thread to periodically perform an action.

  You can use this object to periodically perform tasks within a python
  application. It does not require polling on file descriptor or having an
  event loop, each task is entirely independent.

  Relies on thread, it has been used within django application to easily manage
  periodic tasks. The main advantage over cron or system schedulers is that it
  is self contained in the application: you start it, you have your periodic
  tasks.  No need to fiddle with system files or to have extra installation
  steps.

  Note that this thread uses dateutil and rrules to compute exactly when the
  next event will need to run, and sleeps in between. It does not continuously
  poll like other implementations.

  Examples:

  Run CheckDatabase every monday at 2:20am
    RecurringEvent(
        "Update Database", OptimizeDatabase, RecurringEvent.WEEKLY,
        repetition={'byweekday': rrule.MO},
        time={'hour': 2, 'minutes': 20})

  Water plants every day at 08:00am:
    RecurringEvent("Water Plants", WaterPlants, RecurringEvent.DAILY,
                   time={'hour': 8, 'minutes': 00})

  Clean temporary files every minute at 15 seconds:
    RecurringEvent("Clean Temp files", self.CleanTempFiles,
                   RecurringEvent.MINUTELY, time={'seconds': 15})   
  """

  # Sleep at most this long. This is defense in depth to prevent missing tasks
  # in case of bugs or to detect changes in timezone / clock or similar.
  MAX_SLEEP_TIME = 600

  YEARLY = rrule.YEARLY
  MONTHLY = rrule.MONTHLY
  WEEKLY = rrule.WEEKLY
  DAILY = rrule.DAILY
  HOURLY = rrule.HOURLY
  MINUTELY = rrule.MINUTELY
  SECONDLY = rrule.SECONDLY

  def __init__(
      self, name, action, frequency, repetition={}, time={}, logger=logging):
    """Instantiates a RecurringEvent object.

    Args:
      name: string, name of the task, used for logging purposes only.
      action: lambda or generally function to invoke periodically.
      frequency: one of RecurringEvent.{YEARLY,MONTHLY,WEEKLY,DAILY,HOURLY,
        MINUTELY,SECONDLY}, to indicate when the event should repeat.
      repetition: dict, passed down as named arguments to the rrule
        constructor. This allows to set more esoteric repetitions.
        See examples, and http://labix.org/python-dateutil.
        Examples: RecurringEvent.MONTHLY, repetition={'bysetposition': -1}
        will run the event the last day of the month.
        RecurringEvent.YEARLY, repetition={'byeaster': 2} will run the event
        the second day after easter every year.
      time: dict, used to set a specific time to run the event at.
        Examples: RecurringEvent.DAILY, time={'hours': 8, 'minutes': 20}
        will run the event at 8:20 daily. RecurringEvent.MINUTELY,
        time={'seconds': 30} will run at every minute and 30 seconds.
        You can use repetition={...} to skip days or hours in cron style.
      logger: object implementing the logging interface, to log events.
        Generally you create the logger with logging.getLogger(...).

    Returns:
      Nothing. It will start running the event as requested, by spawning
      a separate thread.

    Notes:
      Times are always relative to your time zone. If you use this with
      django, don't forget to set the TIME_ZONE parameter in the settings
      file, otherwise you will get confused.
    """
    startdate = datetime.datetime.now(tz.tzlocal())
    if time:
      if set(time) - set(['hours', 'minutes', 'seconds', 'microseconds']):
        raise ValueError("Invalid time dict provided: %s" % time)

      if 'hours' in time:
        replace = (0, 0, 0, 0)
      elif 'minutes' in time:
        replace = (startdate.hour, 0, 0, 0)
      elif 'seconds' in time:
        replace = (startdate.hour, startdate.minute, 0, 0)
      elif 'microseconds' in time:
        replace = (startdate.hour, startdate.minute, startdate.seconds, 0)
      startdate = (startdate.replace(
          startdate.year, startdate.month, startdate.day, *replace)
          + relativedelta.relativedelta(**time))

    self.logger = logger
    self.name = name
    self.rrule = rrule.rrule(frequency, dtstart=startdate, **repetition)
    self.action = action

    self.thread = threading.Thread(name=self.name, target=self.Run)
    self.thread.setDaemon(True)
    self.thread.start()

  def Run(self):
    """Sleeps and run events as requested."""
    iterator = iter(self.rrule)
    event_time = iterator.next()
    tzlocal = tz.tzlocal()

    try:
      # Skip all past events. This can be caused by different ways
      # to specify timing.
      now = datetime.datetime.now(tzlocal)
      while event_time < now:
        event_time = iterator.next()

      self.logger.debug("%s(%s): next occurrence at %s, current time %s",
                        self.thread.ident, self.name, event_time, now)

      # Act for future events.
      while True:
        now = datetime.datetime.now(tzlocal)
        wait = min((event_time - now).total_seconds(), self.MAX_SLEEP_TIME)
        if wait > 0.0:
          self.logger.debug("%s(%s): sleeping for %s",
                            self.thread.ident, self.name, wait)
          time.sleep(wait)
        
        now = datetime.datetime.now(tzlocal)
        if now >= event_time:
          self.logger.info("%s(%s): invoking callback now "
                           "(current time: %s, event time: %s)",
                           self.thread.ident, self.name, now, event_time)
          self.action()
          event_time = iterator.next()

          self.logger.debug("%s(%s): next occurrence at %s, current time %s",
                            self.thread.ident, self.name, event_time, now)
    except StopIteration:
      return
