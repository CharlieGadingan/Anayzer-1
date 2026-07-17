import ast
import os
import re
import subprocess
import sys
import tempfile
from xml.sax.saxutils import escape as xml_escape


LANGUAGE_SPECS = {
    'c': {
        'label': 'C',
        'extensions': ['.c'],
    },
    'cpp': {
        'label': 'C++',
        'extensions': ['.cpp', '.cc', '.cxx', '.c++', '.h', '.hpp'],
    },
    'java': {
        'label': 'Java',
        'extensions': ['.java'],
    },
    'javascript': {
        'label': 'JavaScript',
        'extensions': ['.js', '.mjs', '.cjs'],
    },
    'csharp': {
        'label': 'C#',
        'extensions': ['.cs'],
    },
    'python': {
        'label': 'Python',
        'extensions': ['.py'],
    },
}


def _build_extension_map():
    mapping = {}
    for language, spec in LANGUAGE_SPECS.items():
        for extension in spec['extensions']:
            mapping[extension] = language
    return mapping


EXTENSION_TO_LANGUAGE = _build_extension_map()
SUPPORTED_LANGUAGES = [spec['label'] for spec in LANGUAGE_SPECS.values()]
SUPPORTED_EXTENSIONS = sorted({ext for spec in LANGUAGE_SPECS.values() for ext in spec['extensions']})


def detect_language(file_name):
    """Return the internal language key for a filename, or None if unsupported."""
    extension = os.path.splitext(file_name)[1].lower()
    return EXTENSION_TO_LANGUAGE.get(extension)


def get_missing_tool_hint(language):
    if language == 'c':
        return 'gcc'
    if language == 'cpp':
        return 'g++'
    if language == 'java':
        return 'javac'
    if language == 'javascript':
        return 'node'
    if language == 'csharp':
        return 'dotnet'
    if language == 'python':
        return 'python'
    return 'a supported compiler'


def build_analysis_command(language, file_path, repo_root=None):
    """Build the syntax-check command for a supported language."""
    cleanup_paths = []

    if language == 'c':
        return ['gcc', '-fsyntax-only', '-Wall', '-Wextra', '-std=c11', file_path], cleanup_paths

    if language == 'cpp':
        return ['g++', '-fsyntax-only', '-Wall', '-Wextra', '-std=c++14', file_path], cleanup_paths

    if language == 'java':
        output_dir = tempfile.mkdtemp(prefix='java-syntax-')
        cleanup_paths.append(output_dir)
        source_files = _collect_java_sources(file_path, repo_root)
        source_list_path = os.path.join(output_dir, 'sources.txt')
        with open(source_list_path, 'w', encoding='utf-8') as source_list:
            for source_file in source_files:
                source_list.write(_format_javac_argfile_path(source_file) + '\n')

        source_root = repo_root or os.path.dirname(file_path)
        return [
            'javac',
            '-proc:none',
            '-Xlint:all',
            '-d', output_dir,
            '-sourcepath', source_root,
            f'@{source_list_path}'
        ], cleanup_paths

    if language == 'javascript':
        return ['node', '--check', file_path], cleanup_paths

    if language == 'csharp':
        project_dir = tempfile.mkdtemp(prefix='csharp-syntax-')
        cleanup_paths.append(project_dir)
        project_path = os.path.join(project_dir, 'SyntaxCheck.csproj')
        project_root = os.path.abspath(project_dir).replace('\\', '/')
        compile_files = [os.path.abspath(file_path).replace('\\', '/')]

        if repo_root and os.path.isdir(repo_root):
            compile_files = []
            for root, dirs, files in os.walk(repo_root):
                dirs[:] = [
                    d for d in dirs
                    if not d.startswith('.') and d not in ['bin', 'obj', 'build', 'dist', 'node_modules']
                ]
                for name in files:
                    if name.lower().endswith('.cs'):
                        compile_files.append(os.path.abspath(os.path.join(root, name)).replace('\\', '/'))

            if not compile_files:
                compile_files = [os.path.abspath(file_path).replace('\\', '/')]

        compile_items_lines = []
        for path in compile_files:
            escaped_path = xml_escape(path).replace('"', '&quot;')
            compile_items_lines.append(f'    <Compile Include="{escaped_path}" />')
        compile_items = '\n'.join(compile_items_lines)
        project_xml = f'''<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <OutputType>Library</OutputType>
    <LangVersion>latest</LangVersion>
    <Nullable>disable</Nullable>
    <ImplicitUsings>disable</ImplicitUsings>
    <TreatWarningsAsErrors>false</TreatWarningsAsErrors>
    <EnableDefaultCompileItems>false</EnableDefaultCompileItems>
    <BaseOutputPath>{project_root}/bin/</BaseOutputPath>
    <BaseIntermediateOutputPath>{project_root}/obj/</BaseIntermediateOutputPath>
  </PropertyGroup>
  <ItemGroup>
{compile_items}
  </ItemGroup>
</Project>
'''
        with open(project_path, 'w', encoding='utf-8') as project_file:
            project_file.write(project_xml)
        return ['dotnet', 'build', project_path, '--nologo', '-clp:NoSummary'], cleanup_paths

    raise ValueError(f'Unsupported language: {language}')


def _collect_java_sources(file_path, repo_root=None):
    if not repo_root or not os.path.isdir(repo_root):
        return [os.path.abspath(file_path)]

    ignored_dirs = {
        '.git',
        '.gradle',
        '.idea',
        '.mvn',
        'build',
        'dist',
        'node_modules',
        'out',
        'target',
    }
    source_files = []

    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ignored_dirs]
        for name in files:
            if name.lower().endswith('.java'):
                source_files.append(os.path.abspath(os.path.join(root, name)))

    target_path = os.path.abspath(file_path)
    if target_path not in source_files:
        source_files.append(target_path)

    return sorted(source_files)


def _format_javac_argfile_path(path):
    normalized_path = os.path.abspath(path)
    return '"' + normalized_path.replace('\\', '\\\\').replace('"', '\\"') + '"'


_STANDARD_DIAGNOSTIC_RE = re.compile(
    r'^(.*?):(\d+)(?::(\d+))?:\s+(fatal error|error|warning):\s+(.*)$',
    re.IGNORECASE
)
_CSHARP_DIAGNOSTIC_RE = re.compile(
    r'^(.*)\((\d+)(?:,(\d+))?\):\s+(error|warning)\s+([A-Z]+\d+):\s+(.*)$',
    re.IGNORECASE
)


def _add_diagnostic(errors, warnings, kind, line, message):
    entry = {
        'line': int(line) if line else 0,
        'message': message.strip(),
        'type': kind,
    }
    if kind == 'warning':
        warnings.append(entry)
    else:
        errors.append(entry)


def _same_source_path(diagnostic_path, source_path):
    if not diagnostic_path or not source_path:
        return True

    normalized_diagnostic = os.path.normcase(os.path.abspath(diagnostic_path))
    normalized_source = os.path.normcase(os.path.abspath(source_path))
    return normalized_diagnostic == normalized_source


def _parse_standard_output(output, source_path=None):
    errors = []
    warnings = []
    seen = set()

    for line in output.splitlines():
        match = _STANDARD_DIAGNOSTIC_RE.match(line.strip())
        if not match:
            continue

        if source_path and not _same_source_path(match.group(1), source_path):
            continue

        line_num = int(match.group(2))
        kind = 'warning' if match.group(4).lower() == 'warning' else 'error'
        message = match.group(5).strip()
        key = (line_num, kind, message)
        if key in seen:
            continue
        seen.add(key)
        _add_diagnostic(errors, warnings, kind, line_num, message)

    return errors, warnings


def _parse_csharp_output(output):
    errors = []
    warnings = []
    seen = set()

    for line in output.splitlines():
        match = _CSHARP_DIAGNOSTIC_RE.match(line.strip())
        if not match:
            continue

        line_num = int(match.group(2))
        kind = match.group(4).lower()
        code = match.group(5).strip()
        message = match.group(6).strip()
        full_message = f'{code}: {message}'
        key = (line_num, kind, full_message)
        if key in seen:
            continue
        seen.add(key)
        _add_diagnostic(errors, warnings, kind, line_num, full_message)

    return errors, warnings


def _parse_javascript_output(output):
    errors = []
    warnings = []
    seen = set()
    pending_line = 0

    for raw_line in output.splitlines():
        line = raw_line.strip()
        location_match = re.match(r'^(.*?):(\d+)(?::(\d+))?$', line)
        if location_match:
            pending_line = int(location_match.group(2))
            continue

        if any(token in line for token in ('SyntaxError:', 'ReferenceError:', 'TypeError:', 'RangeError:', 'EvalError:', 'URIError:')):
            message = line.split(':', 1)[1].strip() if ':' in line else line
            key = (pending_line, 'error', message)
            if key not in seen:
                seen.add(key)
                _add_diagnostic(errors, warnings, 'error', pending_line, message)
            pending_line = 0

    return errors, warnings


def _parse_python_output(output, source_path):
    errors = []
    warnings = []
    seen = set()
    pending_line = 0

    for raw_line in output.splitlines():
        line = raw_line.strip()

        file_match = re.match(r'^File "(.+)", line (\d+)(?:, in .+)?$', line)
        if file_match:
            pending_line = int(file_match.group(2))
            continue

        if any(token in line for token in ('SyntaxError:', 'IndentationError:', 'TabError:')):
            message = line.split(':', 1)[1].strip() if ':' in line else line
            key = (pending_line, 'error', message)
            if key not in seen:
                seen.add(key)
                _add_diagnostic(errors, warnings, 'error', pending_line, message)
            pending_line = 0

    return errors, warnings


def parse_diagnostics(language, output, file_path):
    if not output.strip():
        return [], []

    if language in {'c', 'cpp'}:
        return _parse_standard_output(output)
    if language == 'java':
        return _parse_standard_output(output, file_path)
    if language == 'csharp':
        return _parse_csharp_output(output)
    if language == 'javascript':
        return _parse_javascript_output(output)
    if language == 'python':
        return _parse_python_output(output, file_path)
    return [], []


def _run_python_syntax_check(file_path):
    errors = []
    warnings = []

    try:
        with open(file_path, 'r', encoding='utf-8') as source_file:
            source = source_file.read()
    except UnicodeDecodeError:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as source_file:
            source = source_file.read()

    try:
        ast.parse(source, filename=file_path)
    except SyntaxError as exc:
        errors.append({
            'line': exc.lineno or 0,
            'message': exc.msg or 'Syntax error',
            'type': 'error',
        })
    except Exception as exc:
        errors.append({
            'line': 0,
            'message': f'Analysis error: {exc}',
            'type': 'error',
        })

    return errors, warnings, ''


def analyze_syntax(file_path, language, repo_root=None, timeout=30):
    """Run a language-appropriate syntax check for a single file."""
    if language == 'python':
        return _run_python_syntax_check(file_path)

    errors = []
    warnings = []
    cleanup_paths = []
    compile_output = ''

    try:
        cmd, cleanup_paths = build_analysis_command(language, file_path, repo_root)
        process = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        compile_output = '\n'.join(part for part in [process.stdout, process.stderr] if part)
        errors, warnings = parse_diagnostics(language, compile_output, file_path)

        if process.returncode != 0 and not errors:
            fallback_message = compile_output.strip() or f'{language} syntax check failed with exit code {process.returncode}'
            errors.append({
                'line': 0,
                'message': fallback_message,
                'type': 'error',
            })
    except subprocess.TimeoutExpired:
        errors.append({
            'line': 0,
            'message': f'Compilation timeout after {timeout} seconds',
            'type': 'error',
        })
    except FileNotFoundError:
        errors.append({
            'line': 0,
            'message': f'Compiler not found. Please install {get_missing_tool_hint(language)}.',
            'type': 'error',
        })
    except Exception as exc:
        errors.append({
            'line': 0,
            'message': f'Analysis error: {exc}',
            'type': 'error',
        })
    finally:
        for path in cleanup_paths:
            if os.path.isdir(path):
                try:
                    import shutil
                    shutil.rmtree(path, ignore_errors=True)
                except Exception:
                    pass
            elif os.path.exists(path):
                try:
                    os.unlink(path)
                except Exception:
                    pass

    return errors, warnings, compile_output
