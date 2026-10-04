"""Read-only XLSX values with bounded, conservative display semantics.

No formulas, macros, external links or formatting code are executed. Unknown
formats remain raw and explicit; saved formula values are never recalculated.
"""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
import io
import json
import posixpath
import re
import unicodedata
import zipfile

from .document_preview import _check_directory, _read_xml

MAX_CHARS = 200000
MAX_CELL_CHARS = 2000
BUILTIN_FORMATS = {
    0: 'General', 1: '0', 2: '0.00', 3: '#,##0', 4: '#,##0.00',
    9: '0%', 10: '0.00%', 14: 'mm-dd-yy', 15: 'd-mmm-yy',
    16: 'd-mmm', 17: 'mmm-yy', 18: 'h:mm AM/PM', 19: 'h:mm:ss AM/PM',
    20: 'h:mm', 21: 'h:mm:ss', 22: 'm/d/yy h:mm',
    37: '#,##0;(#,##0)', 38: '#,##0;[Red](#,##0)',
    39: '#,##0.00;(#,##0.00)', 40: '#,##0.00;[Red](#,##0.00)', 49: '@',
}
COLORS = {'black', 'blue', 'cyan', 'green', 'magenta', 'red', 'white', 'yellow'}


def _tag(node):
    return node.tag.rsplit('}', 1)[-1]


def _child(node, name):
    return next((item for item in node if _tag(item) == name), None)


def _sections(code):
    sections, part, quoted, escaped = [], [], False, False
    for char in code:
        if char == ';' and not quoted and not escaped:
            sections.append(''.join(part)); part = []
        else:
            part.append(char)
        if char == '"' and not escaped:
            quoted = not quoted
        escaped = char == '\\' and not escaped
    if quoted or escaped:
        raise ValueError('Incomplete number format')
    sections.append(''.join(part))
    if len(sections) > 4:
        raise ValueError('Too many number format sections')
    return sections


def _tokens(code):
    """Keep quoted literals distinct from active numeric/date placeholders."""
    result, index = [], 0
    while index < len(code):
        char = code[index]
        if char == '"':
            end = code.find('"', index + 1)
            if end < 0:
                raise ValueError('Incomplete literal')
            result.append(('literal', code[index + 1:end])); index = end + 1
        elif char == '\\':
            if index + 1 >= len(code):
                raise ValueError('Incomplete escape')
            result.append(('literal', code[index + 1])); index += 2
        elif char in '_*':
            if index + 1 >= len(code):
                raise ValueError('Incomplete spacing')
            index += 2  # Column-width fill/spacing does not change the value.
        elif char == '[':
            end = code.find(']', index + 1)
            if end < 0:
                raise ValueError('Incomplete format modifier')
            modifier = code[index + 1:end]
            if modifier.lower() in COLORS:
                pass
            elif modifier.startswith('$') and re.fullmatch(r'\$[^\[\]<>=";]*?(?:-[0-9A-Fa-f]+)?', modifier):
                currency = re.sub(r'-[0-9A-Fa-f]+$', '', modifier[1:])
                if currency:
                    result.append(('literal', currency))
            else:
                raise ValueError('Unsupported condition or elapsed-time format')
            index = end + 1
        else:
            result.append(('code', char)); index += 1
    return result


def _date_value(value, bare, date1904):
    skeleton = bare.lower().replace('am/pm', '').replace('a/p', '').strip()
    if not re.fullmatch(r'[ymdhs :/.,\-]+', skeleton):
        raise ValueError('Unsupported date format')
    has_date = 'y' in skeleton or 'd' in skeleton
    has_time = 'h' in skeleton or 's' in skeleton
    if not has_date and not re.fullmatch(r'hh?:mm(?::ss)?', skeleton):
        raise ValueError('Unsupported time format')
    if has_time and not re.search(r'hh?:mm(?::ss)?', skeleton):
        raise ValueError('Unsupported time fields')
    if value < 0 or value > 2958465:
        raise ValueError('Date outside supported range')
    days = int(value)
    seconds = 0
    if has_time:
        # Hidden seconds do not round the minute field (12:00:59 stays 12:00).
        seconds = int(((value - days) * 86400).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        if seconds == 86400:
            # Excel renderers can display hour 24 without changing the date.
            # Our normalized clock must not invent a different calendar day.
            raise ValueError('Ambiguous rounding across midnight')
    if has_date:
        # Excel's fictional 1900-02-29 is not silently mapped to a real date.
        if not date1904 and (days < 1 or days == 60):
            raise ValueError('Excel date has no Gregorian equivalent')
        epoch = date(1904, 1, 1) if date1904 else date(1899, 12, 31) if days < 60 else date(1899, 12, 30)
        stamp = (epoch + timedelta(days=days)).isoformat()
    else:
        stamp = ''
    if has_time:
        clock = f'{seconds // 3600:02}:{seconds // 60 % 60:02}'
        if 's' in skeleton:
            clock += f':{seconds % 60:02}'
        stamp += (' ' if stamp else '') + clock
    return stamp


def _number_value(raw, code, date1904):
    if code is None or len(code) > 255:
        return raw, 'number', 'unsupported'
    if code.lower() in ('general', '@'):
        return raw, 'number', 'raw'
    try:
        if len(raw) > 100:
            raise ValueError('Number too long')
        with localcontext() as context:
            context.prec = 100
            value = Decimal(raw)
            if not value.is_finite() or abs(value.adjusted()) > 80:
                raise ValueError('Unsupported numeric magnitude')
            sections = _sections(code)
            # Conditions replace the usual positive/negative/zero selection.
            # Validate every section before selecting, including unused ones.
            parsed = [_tokens(section) for section in sections]
            tokens = parsed[1] if value < 0 and len(parsed) > 1 else parsed[2] if value == 0 and len(parsed) > 2 else parsed[0]
            bare = ''.join(text for kind, text in tokens if kind == 'code')
            if re.search(r'[ymdhs]', bare, re.I):
                if any(kind == 'literal' and not re.fullmatch(r'[\s/\-:.,年月日时分秒]*', text) for kind, text in tokens):
                    raise ValueError('Date literal cannot be normalized without losing meaning')
                return _date_value(value, bare, date1904), 'date', 'formatted'
            if re.search(r'[A-Za-z]', bare):
                raise ValueError('Unsupported number notation')
            # A placeholder may not straddle a quoted literal or modifier.
            skeleton = ''.join(text if kind == 'code' else '\x00' for kind, text in tokens)
            matches = list(re.finditer(r'(?:#,##)?0+(?:\.0*#*)?', skeleton))
            if not matches:
                if all(kind == 'literal' or text.isspace() for kind, text in tokens):
                    return ''.join(text for _, text in tokens), 'number', 'formatted'
                raise ValueError('Missing number pattern')
            if len(matches) != 1:
                raise ValueError('Ambiguous number pattern')
            match = matches[0]
            # Tokens are one code character or one literal, so skeleton indices
            # match token indices. Only explicit affixes are allowed.
            affixes = tokens[:match.start()] + tokens[match.end():]
            if any(kind == 'code' and not (text.isspace() or text in '+-()%' or unicodedata.category(text) == 'Sc') for kind, text in affixes):
                raise ValueError('Unsupported scaling or placeholder')
            percent = sum(kind == 'code' and text == '%' for kind, text in affixes)
            if percent > 1:
                raise ValueError('Repeated percent scaling')
            numeric = match.group()
            integer_pattern, _, decimal_pattern = numeric.partition('.')
            if len(decimal_pattern) > 10 or not re.fullmatch(r'0*#*', decimal_pattern):
                raise ValueError('Unsupported decimal precision')
            magnitude = abs(value) * (100 if percent else 1)
            quantum = Decimal(1).scaleb(-len(decimal_pattern))
            text = format(magnitude.quantize(quantum, rounding=ROUND_HALF_UP), 'f')
            integer, _, fraction = text.partition('.')
            integer = integer.zfill(integer_pattern.count('0'))
            if ',' in integer_pattern:
                groups = [integer[max(0, index - 3):index] for index in range(len(integer), 0, -3)]
                integer = ','.join(reversed(groups))
            while len(fraction) > decimal_pattern.count('0') and fraction.endswith('0'):
                fraction = fraction[:-1]
            display = ''.join(text for _, text in tokens[:match.start()]) + integer + ('.' + fraction if fraction else '') + ''.join(text for _, text in tokens[match.end():])
            if value < 0 and len(sections) == 1:
                display = '-' + display
            return display, 'number', 'formatted'
    except (ValueError, InvalidOperation, OverflowError):
        return raw, 'number', 'unsupported'


def _styles(archive):
    if 'xl/styles.xml' not in archive.namelist():
        return ['General']
    root = _read_xml(archive, 'xl/styles.xml')
    custom = {}
    for item in root.iter():
        if _tag(item) == 'numFmt' and (item.get('numFmtId') or '').isdigit() and len(item.get('numFmtId')) < 10:
            custom[int(item.get('numFmtId'))] = item.get('formatCode', '')
    base = _child(root, 'cellStyleXfs')
    inherited = list(base) if base is not None else []
    formats = []
    xfs = _child(root, 'cellXfs')
    for item in xfs if xfs is not None else []:
        # Explicit nonapplication needs full style inheritance semantics. Avoid
        # presenting an inferred format as faithful to the workbook.
        if item.get('applyNumberFormat', '').lower() in ('0', 'false'):
            formats.append(None)
            continue
        number = item.get('numFmtId')
        parent = item.get('xfId')
        if number is None and parent is not None:
            if not parent.isdigit() or len(parent) >= 10 or int(parent) >= len(inherited):
                formats.append(None)
                continue
            number = inherited[int(parent)].get('numFmtId', '0')
        elif number is None:
            number = '0'
        identifier = int(number) if number and number.isdigit() and len(number) < 10 else -1
        formats.append(custom.get(identifier, BUILTIN_FORMATS.get(identifier)))
    return formats or ['General']


def _cell(node, strings, formats, date1904):
    value_node, formula_node = _child(node, 'v'), _child(node, 'f')
    raw = value_node.text or '' if value_node is not None else ''
    cell_type, clipped = node.get('t'), False
    style = node.get('s', '0')
    code = formats[int(style)] if style.isdigit() and len(style) < 10 and int(style) < len(formats) else None
    if cell_type == 's':
        raw = strings[int(raw)] if raw.isdigit() and len(raw) < 10 and int(raw) < len(strings) else ''
    elif cell_type == 'inlineStr':
        raw = ''.join(item.text or '' for item in node.iter() if _tag(item) == 't')
    if cell_type in ('s', 'inlineStr', 'str'):
        display, kind, status = raw, 'text', None
    elif cell_type == 'b':
        display, kind, status = {'0': 'FALSE', '1': 'TRUE'}.get(raw, raw), 'boolean', None
    elif cell_type == 'e':
        display, kind, status = raw, 'error', None
    elif cell_type == 'd':
        display, kind, status = raw, 'date', 'raw'
    elif raw:
        display, kind, status = _number_value(raw, code, date1904)
    else:
        display, kind, status = '', 'text', None
    cell = {'raw': raw, 'display': display, 'kind': kind}
    if status is not None:
        cell['format_status'] = status
        if code is not None:
            cell['number_format'] = code[:255]
            clipped = len(code) > 255
    if formula_node is not None:
        formula = formula_node.text or ''
        saved = value_node is not None and (bool(raw) or cell_type == 'str')
        cell.update(kind='formula', formula_status='saved' if saved else 'missing')
        if formula:
            cell['formula'] = formula
        if not saved:
            cell['display'] = '=' + formula if formula else ''
    for field in ('raw', 'display', 'formula'):
        if field in cell and len(cell[field]) > MAX_CELL_CHARS:
            cell[field] = cell[field][:MAX_CELL_CHARS]
            clipped = True
    return cell, clipped


def _size(value):
    return len(json.dumps(value, ensure_ascii=False))


def xlsx_preview(data):
    _check_directory(data)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) > 2048 or sum(item.file_size for item in entries) > 32 * 1024 * 1024:
            raise ValueError('Preview too large')
        if len({item.filename for item in entries}) != len(entries):
            raise ValueError('Duplicate archive entry')
        workbook = _read_xml(archive, 'xl/workbook.xml')
        properties = _child(workbook, 'workbookPr')
        date1904 = properties is not None and properties.get('date1904', '').lower() in ('1', 'true')
        relationships = _read_xml(archive, 'xl/_rels/workbook.xml.rels')
        targets = {item.get('Id'): item.get('Target', '') for item in relationships if item.get('TargetMode') != 'External'}
        formats = _styles(archive)
        strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            strings = [''.join(part.text or '' for part in item.iter() if _tag(part) == 't') for item in _read_xml(archive, 'xl/sharedStrings.xml')]
        result = {'kind': 'spreadsheet', 'sheets': [], 'truncated': False}
        # Include duplicate rows/display text and all metadata in the same budget.
        used, exhausted = _size(result) + 128, False
        definitions = [item for item in workbook.iter() if _tag(item) == 'sheet']
        for definition in definitions:
            if len(result['sheets']) >= 6 or exhausted:
                result['truncated'] = True
                break
            relation = next((value for key, value in definition.attrib.items() if key.endswith('}id')), '')
            target = targets.get(relation, '')
            path = posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join('xl', target))
            if not path.startswith('xl/worksheets/') or not path.endswith('.xml'):
                result['truncated'] = True
                continue
            root = _read_xml(archive, path)
            if any(_tag(item) == 'f' for item in root.iter()):
                result['formula_results'] = 'not_recalculated'
            name = definition.get('name', '')
            sheet = {'name': name[:256], 'rows': [], 'cells': [], 'row_numbers': [], 'truncated': len(name) > 256}
            sheet_cost = _size(sheet) + 2
            if used + sheet_cost > MAX_CHARS:
                exhausted = True
                break
            used += sheet_cost
            for row in (item for item in root.iter() if _tag(item) == 'row'):
                if len(sheet['rows']) >= 200 or exhausted:
                    sheet['truncated'] = True
                    break
                row_number = row.get('r', '')
                row_number = int(row_number) if row_number.isdigit() and len(row_number) < 8 else len(sheet['rows']) + 1
                row_cost = 16 + len(str(row_number))
                if used + row_cost > MAX_CHARS:
                    exhausted = True; sheet['truncated'] = True
                    break
                used += row_cost
                cells = []
                for node in (item for item in row if _tag(item) == 'c'):
                    reference = re.fullmatch(r'([A-Z]{1,3})[0-9]+', node.get('r', ''))
                    column = len(cells)
                    if reference:
                        column = 0
                        for char in reference[1]:
                            column = column * 26 + ord(char) - ord('A') + 1
                        column -= 1
                    if column >= 40 or column < len(cells):
                        sheet['truncated'] = True
                        continue
                    cell, clipped = _cell(node, strings, formats, date1904)
                    padding = [{'raw': '', 'display': '', 'kind': 'text'} for _ in range(column - len(cells))]
                    addition = [*padding, cell]
                    cost = sum(_size(item) + _size(item['display']) + 4 for item in addition)
                    if used + cost > MAX_CHARS:
                        exhausted = True; sheet['truncated'] = True
                        break
                    used += cost; cells.extend(addition)
                    sheet['truncated'] = sheet['truncated'] or clipped
                sheet['cells'].append(cells)
                sheet['rows'].append([cell['display'] for cell in cells])
                sheet['row_numbers'].append(row_number)
            result['sheets'].append(sheet)
        if exhausted:
            result['truncated'] = True
        calc = _child(workbook, 'calcPr')
        if calc is not None and (calc.get('calcMode') == 'manual' or any(calc.get(name, '').lower() in ('1', 'true') for name in ('fullCalcOnLoad', 'forceFullCalc'))):
            result['calculation_warning'] = True
        return result
