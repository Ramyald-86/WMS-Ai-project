"""Bounded Excel inputs and pallet positions for box receiving."""
import re
from app.services.workflow_rules import workbook, sheet_rows, quantity, text_value, barcode

ALIASES = {
    'container': {'КОРОБ','КОРОБА','СПИСОККОРОБОВ','НАЗВАНИЕКОРОБА','ШККОРОБА','BOX','SSCC','CONTAINER'},
    'barcode': {'ШК','ШКТОВАРА','ШТРИХКОД','ШТРИХКОДТОВАРА','BARCODE','EAN','EAN13','GTIN'},
    'qty': {'КОЛИЧЕСТВО','КОЛВО','КОЛИЧЕСТВОШТ','QTY','QUANTITY','НУЖНО','СОБРАТЬ'},
    'article': {'АРТИКУЛ','АРТИКУЛТОВАРА','АРТИКУЛПОСТАВЩИКА','АРТИКУЛПРОДАВЦА','ARTICLE','VENDORCODE'},
    'size': {'РАЗМЕР','SIZE','TECHSIZE'},
    'location': {'ЯЧЕЙКА','АДРЕС','КОДЯЧЕЙКИ','ШКЯЧЕЙКИ','LOCATION','LOCATIONCODE'},
}


# Keyboard-layout correction, not transliteration: ИЩЧ -> BOX.
_RU = 'ё1234567890-=йцукенгшщзхъфывапролджэячсмитьбю'
_EN = '`1234567890-=qwertyuiop[]asdfghjkl;\'zxcvbnm,.'
_LAYOUT = dict(zip(_RU, _EN))
_LAYOUT.update(dict(zip('ЁЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ', '~QWERTYUIOP{}ASDFGHJKL:"ZXCVBNM<>')))


def normalize_box_scan(value):
    value = str(value or '').strip()
    return ''.join(_LAYOUT.get(ch, ch) for ch in value)


def box_code(value):
    if isinstance(value, bool): raise ValueError('Нужен текстовый ШК короба')
    if isinstance(value, (int,float)):
        if value != int(value) or abs(value)>999999999999999:
            raise ValueError('Длинный ШК короба сохраните в Excel как текст, чтобы не потерять цифры')
        value = str(int(value))
    result = text_value(normalize_box_scan(value),120)
    if not result: raise ValueError('Укажите ШК или название короба')
    if result.upper().startswith('FCLOC-'): raise ValueError('Это код адреса. Отсканируйте короб')
    return result


def position(number, first_row=1):
    if type(number) is not int or number<1: raise ValueError('Номер места должен быть положительным')
    if first_row is None: return None, None
    if type(first_row) is not int or first_row<1 or first_row>900:
        raise ValueError('Первый ряд должен быть от 1 до 900')
    return first_row+(number-1)//8, (number-1)%8+1


def read_sheet(content, kind='goods'):
    if kind not in ('goods','layout','boxes'): raise ValueError('Неизвестный тип файла')
    wb=workbook(content)
    try:
        values=sheet_rows(wb.active);mapping=None;start=None
        for ix,row in enumerate(values[:20]):
            m={}
            for col,v in enumerate(row):
                label=re.sub(r'[^A-ZА-Я0-9]','',str(v or '').upper().replace('Ё','Е'))
                for key,aliases in ALIASES.items():
                    if label in aliases:
                        if key in m: raise ValueError(f'Колонка {v} указана дважды')
                        m[key]=col
            valid=('container' in m) if kind=='boxes' else ('qty' in m and ('barcode' in m or 'article' in m))
            if valid:
                mapping=m;start=ix+1;break
        if mapping is None: raise ValueError('Не найдены заголовки. Скачайте шаблон выбранного сценария')
        if kind=='layout' and 'container' not in mapping: raise ValueError('Для раскладки нужна колонка «Короб»')
        out=[];seen=set()
        for ix,row in enumerate(values[start:],start=start+1):
            if not any(v not in (None,'') for v in row):continue
            def cell(k):
                j=mapping.get(k);return row[j] if j is not None and j<len(row) else None
            try:
                box=box_code(cell('container')) if cell('container') not in (None,'') else ''
                if kind=='boxes':
                    if not box:raise ValueError('Не указан короб')
                    if box in seen:raise ValueError(f'Короб {box} повторяется в задании')
                    seen.add(box);out.append({'row':ix,'container':box});continue
                code=barcode(cell('barcode')) if cell('barcode') not in (None,'') else ''
                article=text_value(cell('article'),120);size=text_value(cell('size'),80)
                if not code and not article:raise ValueError('Укажите ШК товара или артикул')
                if kind=='layout' and (not code or not box):raise ValueError('В раскладке обязательны ШК товара и короб')
                out.append({'row':ix,'barcode':code,'article':article,'size':size,'qty':quantity(cell('qty')),
                            'container':box,'location':text_value(cell('location'),120)})
            except ValueError as e:raise ValueError(f'Строка {ix}: {e}')
        if not out:raise ValueError('В файле нет строк задания')
        return out
    finally:wb.close()
