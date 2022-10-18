from pathlib import Path
import sys


CURDIR = Path(__file__).absolute().parent
TRANSLATIONS = CURDIR / 'src/Appendices/Translations.rst'
LOCALIZATION = CURDIR / 'src/CreatingTestData/TestDataSyntax.rst'

sys.path.insert(0, str(CURDIR / '../../src'))


from robot.api import Language


class LanguageWrapper:

    def __init__(self, lang):
        self.lang = lang

    def __getattr__(self, name):
        value = getattr(self.lang, name)
        return value if value is not None else ''

    @property
    def underline(self):
        width = len(self.lang.name + self.lang.code) + 3
        return '-' * width

    @property
    def given_prefixes(self):
        return ', '.join(self.lang.given_prefixes)

    @property
    def when_prefixes(self):
        return ', '.join(self.lang.when_prefixes)

    @property
    def then_prefixes(self):
        return ', '.join(self.lang.then_prefixes)

    @property
    def and_prefixes(self):
        return ', '.join(self.lang.and_prefixes)

    @property
    def but_prefixes(self):
        return ', '.join(self.lang.but_prefixes)

    @property
    def true_strings(self):
        return ', '.join(self.lang.true_strings)

    @property
    def false_strings(self):
        return ', '.join(self.lang.false_strings)


TEMPLATE = '''
{lang.name} ({lang.code})
{lang.underline}

Section headers
~~~~~~~~~~~~~~~

.. list-table::
    :class: tabular
    :width: 40em
    :widths: 2 3
    :header-rows: 1

    * - Header
      - Translation
    * - Settings
      - {lang.settings_header}
    * - Variables
      - {lang.variables_header}
    * - Test Cases
      - {lang.test_cases_header}
    * - Tasks
      - {lang.tasks_header}
    * - Keywords
      - {lang.keywords_header}
    * - Comments
      - {lang.comments_header}

Settings
~~~~~~~~

.. list-table::
    :class: tabular
    :width: 40em
    :widths: 2 3
    :header-rows: 1

    * - Setting
      - Translation
    * - Library
      - {lang.library_setting}
    * - Resource
      - {lang.resource_setting}
    * - Variables
      - {lang.variables_setting}
    * - Documentation
      - {lang.documentation_setting}
    * - Metadata
      - {lang.metadata_setting}
    * - Suite Setup
      - {lang.suite_setup_setting}
    * - Suite Teardown
      - {lang.suite_teardown_setting}
    * - Test Setup
      - {lang.test_setup_setting}
    * - Task Setup
      - {lang.task_setup_setting}
    * - Test Teardown
      - {lang.test_teardown_setting}
    * - Task Teardown
      - {lang.task_teardown_setting}
    * - Test Template
      - {lang.test_template_setting}
    * - Task Template
      - {lang.task_template_setting}
    * - Test Timeout
      - {lang.test_timeout_setting}
    * - Task Timeout
      - {lang.task_timeout_setting}
    * - Test Tags
      - {lang.test_tags_setting}
    * - Task Tags
      - {lang.task_tags_setting}
    * - Keyword Tags
      - {lang.keyword_tags_setting}
    * - Tags
      - {lang.tags_setting}
    * - Setup
      - {lang.setup_setting}
    * - Teardown
      - {lang.teardown_setting}
    * - Template
      - {lang.template_setting}
    * - Timeout
      - {lang.timeout_setting}
    * - Arguments
      - {lang.arguments_setting}

BDD prefixes
~~~~~~~~~~~~

.. list-table::
    :class: tabular
    :width: 40em
    :widths: 2 3
    :header-rows: 1

    * - Prefix
      - Translation
    * - Given
      - {lang.given_prefixes}
    * - When
      - {lang.when_prefixes}
    * - Then
      - {lang.then_prefixes}
    * - And
      - {lang.and_prefixes}
    * - But
      - {lang.but_prefixes}

Boolean strings
~~~~~~~~~~~~~~~

.. list-table::
    :class: tabular
    :width: 40em
    :widths: 2 3
    :header-rows: 1

    * - True/False
      - Values
    * - True
      - {lang.true_strings}
    * - False
      - {lang.false_strings}
'''


def update_translations():
    languages = sorted([lang for lang in Language.__subclasses__() if lang.code != 'en'],
                       key=lambda lang: lang.code)
    update(TRANSLATIONS, generate_docs(languages))
    update(LOCALIZATION, list_translations(languages))


def generate_docs(languages):
    for lang in languages:
        yield from TEMPLATE.format(lang=LanguageWrapper(lang)).splitlines()


def list_translations(languages):
    yield ''
    for lang in languages:
        yield f'- `{lang.name} ({lang.code})`_'
    yield ''


def update(path: Path, content):
    source = path.read_text(encoding='UTF-8').splitlines()
    with open(path, 'w') as file:
        write(source, file, end_marker='.. START GENERATED CONTENT')
        file.write('.. Generated by translations.py used by ug2html.py.\n')
        write(content, file)
        write(source, file, start_marker='.. END GENERATED CONTENT')


def write(lines, file, start_marker=None, end_marker=None):
    include = not start_marker
    for line in lines:
        if line == start_marker:
            include = True
        if include:
            file.write(line.rstrip() + '\n')
        if line == end_marker:
            include = False
