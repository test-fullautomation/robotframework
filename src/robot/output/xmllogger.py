#  Copyright 2008-2015 Nokia Networks
#  Copyright 2016-     Robot Framework Foundation
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

from robot.utils import get_timestamp, NullMarkupWriter, safe_str, XmlWriter, ThreadSafeDict
from robot.version import get_full_version
from robot.result.visitor import ResultVisitor

from .loggerhelper import IsLogged
import threading
import time
import os

LOG_LEVEL_XML_FILE = "INFO" # output caused by code in this file, depends on this trace level


class _SegmentingWriter:
    """XmlWriter proxy that tracks open elements and supports rotation.

    cuongnht add segmented output: used for the main output.xml writer so
    that during very long runs the file can periodically be sealed into a
    well-formed segment (``<base>_part_NNN.xml``) and writing continues in a
    fresh file with the same open element structure. Replayed elements are
    marked with ``continued="true"`` so that the segment merger knows to
    join them with their counterparts in the previous segment.
    """

    def __init__(self, path):
        self.path = path
        self._writer = XmlWriter(path, write_empty=False, usage='output')
        self._stack = []    # [(tag, attrs dict), ...], root first
        self.segment_paths = []
        self.last_rotation = time.monotonic()

    def start(self, name, attrs=None, newline=True):
        self._stack.append((name, dict(attrs or {})))
        self._writer.start(name, attrs, newline)

    def end(self, name, newline=True):
        if self._stack and self._stack[-1][0] == name:
            self._stack.pop()
        self._writer.end(name, newline)

    def element(self, name, content=None, attrs=None, escape=True, newline=True):
        self._writer.element(name, content, attrs, escape, newline)

    def content(self, content=None, escape=True, newline=False):
        self._writer.content(content, escape, newline)

    def close(self):
        self._writer.close()

    def rotate(self, sealed_path):
        """Seals the current file into ``sealed_path`` and starts a new one."""
        for name, _ in reversed(self._stack):
            self._writer.end(name)
        self._writer.close()
        os.replace(self.path, sealed_path)
        self._writer = XmlWriter(self.path, write_empty=False, usage='output')
        for index, (name, attrs) in enumerate(self._stack):
            attrs = dict(attrs)
            if index > 0:    # roots of segments correspond by position
                attrs['continued'] = 'true'
            self._writer.start(name, attrs)

    def maybe_rotate(self, interval):
        """Rotates when ``interval`` seconds have passed since the last one.

        Must only be called by the thread that owns this writer.
        """
        if time.monotonic() - self.last_rotation < interval:
            return
        base, ext = os.path.splitext(self.path)
        sealed = f'{base}_part_{len(self.segment_paths) + 1:03d}{ext}'
        self.rotate(sealed)
        self.segment_paths.append(sealed)
        self.last_rotation = time.monotonic()

    def finalize(self):
        """Closes all still open elements and the file itself."""
        while self._stack:
            self.end(self._stack[-1][0])
        self.close()


class XmlLogger(ResultVisitor):

    thread_writer_dict = ThreadSafeDict()
    # cuongnht add thread: thread name -> path of its partial output file.
    # Used by threadmerger to graft thread outputs into the main output.xml.
    thread_output_files = ThreadSafeDict()

    def __init__(self, path, log_level=LOG_LEVEL_XML_FILE, rpa=False, generator='Robot',
                 segment_interval=None):
        self._log_message_is_logged = IsLogged(log_level)
        self._error_message_is_logged = IsLogged('WARN')
        writer = self._get_writer(path, rpa, generator)
        # cuongnht add thread: register here as well so that subclasses that
        # override _get_writer (e.g. OutputWriter test doubles) get their own
        # writer from the _writer property instead of a stale earlier one.
        if path and writer is not None:
            XmlLogger.thread_writer_dict['MainThread'] = writer
        self._errors = []
        self.path = path
        self.rpa = rpa
        self.generator = generator
        # cuongnht add segmented output
        self._segment_interval = segment_interval

    def get_level_from_kw_args(self, args=None):
        # args expected to be a 'kw.args' tuple
        supported_levels = ('ERROR', 'WARN', 'USER', 'INFO', 'DEBUG', 'TRACE') # not using LEVELS from loggerhelper.py here, because of more states inside there. A more strict separation is desired here.
        identified_level = LOG_LEVEL_XML_FILE
        if args is None:
            return identified_level
        for arg in args:
            if arg in supported_levels:
                identified_level = arg
                break
        return identified_level


    @property
    def _writer(self):
        if not self.path:
            return NullMarkupWriter()
        thread_name = threading.current_thread().name
        if thread_name not in XmlLogger.thread_writer_dict:
            filename, file_extension = os.path.splitext(self.path)
            thread_path = filename + '_' + thread_name + file_extension
            XmlLogger.thread_output_files[thread_name] = thread_path
            # cuongnht add segmented output: thread writers also support
            # rotation so long living threads get segmented too.
            XmlLogger.thread_writer_dict[thread_name] = _SegmentingWriter(thread_path)
            XmlLogger.thread_writer_dict[thread_name].start('thread', {'name': thread_name,
                                                                       'generator': get_full_version(self.generator),
                                                                       'generated': get_timestamp(),
                                                                       'rpa': 'true' if self.rpa else 'false',
                                                                       'schemaversion': '4'})
        return XmlLogger.thread_writer_dict[threading.current_thread().name]

    def _get_writer(self, path, rpa, generator):
        if not path:
            return NullMarkupWriter()
        # cuongnht add thread: drop stale state from a possible earlier run in
        # the same process so thread files of this run are tracked from scratch.
        XmlLogger.thread_writer_dict.clear()
        XmlLogger.thread_output_files.clear()
        # cuongnht add segmented output: the main writer tracks open elements
        # so that the output can be rotated into segments during long runs.
        writer = _SegmentingWriter(path)
        writer.start('robot', {'generator': get_full_version(generator),
                               'generated': get_timestamp(),
                               'rpa': 'true' if rpa else 'false',
                               'schemaversion': '4'})
        XmlLogger.thread_writer_dict['MainThread'] = writer  # cuongnht add thread
        return writer

    def close(self):
        self.start_errors()
        for msg in self._errors:
            self._write_message(msg)
        self.end_errors()
        self._writer.end('robot')
        self._writer.close()
        self._close_leftover_thread_writers()

    def _close_leftover_thread_writers(self):
        # cuongnht add thread: threads (typically daemons) that are still
        # running when execution ends leave their writers open. Finalize the
        # files here so they are well-formed XML and can be merged/parsed.
        for name in list(XmlLogger.thread_writer_dict):
            if name == 'MainThread':
                continue
            writer = XmlLogger.thread_writer_dict.pop(name, None)
            if writer is None:
                continue
            try:
                if isinstance(writer, _SegmentingWriter):
                    # Closes the whole open element stack, not only the root,
                    # so also files of threads stuck deep inside keywords
                    # remain well-formed.
                    writer.finalize()
                else:
                    writer.end('thread')
                    writer.close()
            except Exception:
                pass

    def set_log_level(self, level):
        return self._log_message_is_logged.set_level(level)

    def message(self, msg):
        if self._error_message_is_logged(msg.level):
            self._errors.append(msg)

    def log_message(self, msg):
        self._maybe_rotate()  # cuongnht add segmented output
        if self._log_message_is_logged(msg.level):
            self._write_message(msg)

    @property
    def segment_paths(self):
        # cuongnht add segmented output: sealed segments of the main writer.
        writer = XmlLogger.thread_writer_dict.get('MainThread')
        if isinstance(writer, _SegmentingWriter):
            return writer.segment_paths
        return []

    def _maybe_rotate(self):
        # cuongnht add segmented output: periodically seal the current
        # thread's output file into a well-formed segment so that a crash
        # during a very long run loses at most one segment interval of log
        # data. Each thread rotates its own writer (the main writer seals
        # output_part_NNN.xml, thread writers output_<name>_part_NNN.xml).
        if not self._segment_interval or not self.path:
            return
        writer = self._writer
        if isinstance(writer, _SegmentingWriter):
            writer.maybe_rotate(self._segment_interval)

    def _write_message(self, msg):
        attrs = {'timestamp': msg.timestamp or 'N/A', 'level': msg.level}
        if msg.html:
            attrs['html'] = 'true'
        self._writer.element('msg', msg.message, attrs)

    def start_keyword(self, kw):
        self._maybe_rotate()  # cuongnht add segmented output

        # inits
        log_kw_start = True
        msg_level = LOG_LEVEL_XML_FILE

        if kw.name == 'BuiltIn.Log':
            # this keyword has it's own log level
            msg_level = self.get_level_from_kw_args(kw.args)

        if self._log_message_is_logged(msg_level):
            log_kw_start = True
        else:
            # suppress the logging because the trace level does not match
            log_kw_start = False

        if log_kw_start is True:
            attrs = {'name': kw.kwname, 'library': kw.libname}
            if kw.type != 'KEYWORD':
                attrs['type'] = kw.type
            if kw.sourcename:
                attrs['sourcename'] = kw.sourcename
            self._writer.start('kw', attrs)
            self._write_list('var', kw.assign)
            self._write_list('arg', [safe_str(a) for a in kw.args])
            self._write_list('tag', kw.tags)
            # Must be after tags to allow adding message when using --flattenkeywords.
            self._writer.element('doc', kw.doc)

    def end_keyword(self, kw):
        self._maybe_rotate()  # cuongnht add segmented output

        # inits
        log_kw_end = True
        msg_level = LOG_LEVEL_XML_FILE

        if kw.name == 'BuiltIn.Log':
            # this keyword has it's own log level
            msg_level = self.get_level_from_kw_args(kw.args)

        if self._log_message_is_logged(msg_level):
            log_kw_end = True
        else:
            # suppress the logging because the trace level does not match
            log_kw_end = False

        if log_kw_end is True:
            if kw.timeout:
                self._writer.element('timeout', attrs={'value': str(kw.timeout)})
            self._write_status(kw)
            self._writer.end('kw')

    def start_if(self, if_):
        if self._log_message_is_logged(LOG_LEVEL_XML_FILE):
            self._writer.start('if')
            self._writer.element('doc', if_.doc)

    def end_if(self, if_):
        if self._log_message_is_logged(LOG_LEVEL_XML_FILE):
            self._write_status(if_)
            self._writer.end('if')

    def start_if_branch(self, branch):
        if self._log_message_is_logged(LOG_LEVEL_XML_FILE):
            self._writer.start('branch', {'type': branch.type,
                                          'condition': branch.condition})
            self._writer.element('doc', branch.doc)

    def end_if_branch(self, branch):
        if self._log_message_is_logged(LOG_LEVEL_XML_FILE):
            self._write_status(branch)
            self._writer.end('branch')

    def start_for(self, for_):
        if self._log_message_is_logged(LOG_LEVEL_XML_FILE):
            self._writer.start('for', {'flavor': for_.flavor,
                                       'start': for_.start,
                                       'mode': for_.mode,
                                       'fill': for_.fill})
            for name in for_.variables:
                self._writer.element('var', name)
            for value in for_.values:
                self._writer.element('value', value)
            self._writer.element('doc', for_.doc)

    def end_for(self, for_):
        if self._log_message_is_logged(LOG_LEVEL_XML_FILE):
            self._write_status(for_)
            self._writer.end('for')

    def start_thread(self, thread_):
        if threading.current_thread().name == 'MainThread' and self._writer:
            # The _writer property resolves to the main writer on the main
            # thread and to a NullMarkupWriter when output.xml is disabled.
            main_thread_writer = self._writer
            main_thread_writer.start('thread', {'name': thread_.name,
                                                'daemon': str(thread_.daemon)})
            # self._writer.element('name', thread_.name)
            # self._writer.element('daemon', thread_.daemon)
            thread_.result.status = thread_.PASS
            # self._get_thread_writer(thread_.name)
            attrs = {'status': thread_.PASS, 'starttime': thread_.starttime or 'N/A',
                     'endtime': get_timestamp()}
            if not (thread_.starttime and thread_.endtime):
                attrs['elapsedtime'] = str(thread_.elapsedtime)
            main_thread_writer.element('status', thread_.message, attrs)
            # self._write_status(thread_)
            main_thread_writer.element('doc', thread_.doc)
            main_thread_writer.end('thread')

    def end_thread(self, thread_):
        if threading.current_thread() != threading.main_thread():
            self._write_status(thread_)
            self._writer.end('thread')
            thread_name = threading.current_thread().name
            if thread_name in XmlLogger.thread_writer_dict:
                # Close so the file is flushed to disk for the merger.
                XmlLogger.thread_writer_dict.pop(thread_name).close()

    def start_for_iteration(self, iteration):
        if self._log_message_is_logged(LOG_LEVEL_XML_FILE):
            self._writer.start('iter')
            for name, value in iteration.variables.items():
                self._writer.element('var', value, {'name': name})
            self._writer.element('doc', iteration.doc)

    def end_for_iteration(self, iteration):
        if self._log_message_is_logged(LOG_LEVEL_XML_FILE):
            self._write_status(iteration)
            self._writer.end('iter')

    def start_try(self, root):
        if self._log_message_is_logged(LOG_LEVEL_XML_FILE):
            self._writer.start('try')

    def end_try(self, root):
        if self._log_message_is_logged(LOG_LEVEL_XML_FILE):
            self._write_status(root)
            self._writer.end('try')

    def start_try_branch(self, branch):
        if self._log_message_is_logged(LOG_LEVEL_XML_FILE):
            if branch.type == branch.EXCEPT:
                self._writer.start('branch', attrs={
                    'type': 'EXCEPT', 'variable': branch.variable,
                    'pattern_type': branch.pattern_type
                })
                self._write_list('pattern', branch.patterns)
            else:
                self._writer.start('branch', attrs={'type': branch.type})

    def end_try_branch(self, branch):
        if self._log_message_is_logged(LOG_LEVEL_XML_FILE):
            self._write_status(branch)
            self._writer.end('branch')

    def start_while(self, while_):
        if self._log_message_is_logged(LOG_LEVEL_XML_FILE):
            self._writer.start('while', attrs={
                'condition': while_.condition,
                'limit': while_.limit,
                'on_limit': while_.on_limit,
                'on_limit_message': while_.on_limit_message
            })
            self._writer.element('doc', while_.doc)

    def end_while(self, while_):
        if self._log_message_is_logged(LOG_LEVEL_XML_FILE):
            self._write_status(while_)
            self._writer.end('while')

    def start_while_iteration(self, iteration):
        if self._log_message_is_logged(LOG_LEVEL_XML_FILE):
            self._writer.start('iter')
            self._writer.element('doc', iteration.doc)

    def end_while_iteration(self, iteration):
        if self._log_message_is_logged(LOG_LEVEL_XML_FILE):
            self._write_status(iteration)
            self._writer.end('iter')

    def start_return(self, return_):
        if self._log_message_is_logged(LOG_LEVEL_XML_FILE):
            self._writer.start('return')
            for value in return_.values:
                self._writer.element('value', value)

    def end_return(self, return_):
        if self._log_message_is_logged(LOG_LEVEL_XML_FILE):
            self._write_status(return_)
            self._writer.end('return')

    def start_continue(self, continue_):
        if self._log_message_is_logged(LOG_LEVEL_XML_FILE):
            self._writer.start('continue')

    def end_continue(self, continue_):
        if self._log_message_is_logged(LOG_LEVEL_XML_FILE):
            self._write_status(continue_)
            self._writer.end('continue')

    def start_break(self, break_):
        if self._log_message_is_logged(LOG_LEVEL_XML_FILE):
            self._writer.start('break')

    def end_break(self, break_):
        if self._log_message_is_logged(LOG_LEVEL_XML_FILE):
            self._write_status(break_)
            self._writer.end('break')

    def start_error(self, error):
        self._writer.start('error')
        for value in error.values:
            self._writer.element('value', value)

    def end_error(self, error):
        self._write_status(error)
        self._writer.end('error')

    def start_test(self, test):
        self._writer.start('test', {'id': test.id, 'name': test.name,
                                    'line': str(test.lineno or '')})

    def end_test(self, test):
        self._writer.element('doc', test.doc)
        self._write_list('tag', test.tags)
        if test.timeout:
            self._writer.element('timeout', attrs={'value': str(test.timeout)})
        self._write_status(test)
        self._writer.end('test')

    def start_suite(self, suite):
        attrs = {'id': suite.id, 'name': suite.name}
        if suite.source:
            attrs['source'] = str(suite.source)
        self._writer.start('suite', attrs)

    def end_suite(self, suite):
        self._writer.element('doc', suite.doc)
        for name, value in suite.metadata.items():
            self._writer.element('meta', value, {'name': name})
        self._write_status(suite)
        self._writer.end('suite')

    def start_statistics(self, stats):
        self._writer.start('statistics')

    def end_statistics(self, stats):
        self._writer.end('statistics')

    def start_total_statistics(self, total_stats):
        self._writer.start('total')

    def end_total_statistics(self, total_stats):
        self._writer.end('total')

    def start_tag_statistics(self, tag_stats):
        self._writer.start('tag')

    def end_tag_statistics(self, tag_stats):
        self._writer.end('tag')

    def start_suite_statistics(self, tag_stats):
        self._writer.start('suite')

    def end_suite_statistics(self, tag_stats):
        self._writer.end('suite')

    def visit_stat(self, stat):
        self._writer.element('stat', stat.name,
                             stat.get_attributes(values_as_strings=True))

    def start_errors(self, errors=None):
        self._writer.start('errors')

    def end_errors(self, errors=None):
        self._writer.end('errors')

    def _write_list(self, tag, items):
        for item in items:
            self._writer.element(tag, item)

    def _write_status(self, item):
        attrs = {'status': item.status, 'starttime': item.starttime or 'N/A',
                 'endtime': item.endtime or 'N/A'}
        if not (item.starttime and item.endtime):
            attrs['elapsedtime'] = str(item.elapsedtime)
        self._writer.element('status', item.message, attrs)


class FlatXmlLogger(XmlLogger):

    def __init__(self, real_xml_logger):
        super().__init__(None)
        self._writer = real_xml_logger._writer

    def start_keyword(self, kw):
        pass

    def end_keyword(self, kw):
        pass

    def start_for(self, for_):
        pass

    def end_for(self, for_):
        pass

    def start_for_iteration(self, iteration):
        pass

    def end_for_iteration(self, iteration):
        pass

    def start_if(self, if_):
        pass

    def end_if(self, if_):
        pass

    def start_if_branch(self, branch):
        pass

    def end_if_branch(self, branch):
        pass

    def start_try(self, root):
        pass

    def end_try(self, root):
        pass

    def start_try_branch(self, branch):
        pass

    def end_try_branch(self, branch):
        pass

    def start_while(self, while_):
        pass

    def end_while(self, while_):
        pass

    def start_while_iteration(self, iteration):
        pass

    def end_while_iteration(self, iteration):
        pass

    def start_break(self, break_):
        pass

    def end_break(self, break_):
        pass

    def start_continue(self, continue_):
        pass

    def end_continue(self, continue_):
        pass

    def start_return(self, return_):
        pass

    def end_return(self, return_):
        pass

    def start_error(self, error):
        pass

    def end_error(self, error):
        pass
