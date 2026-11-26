import json
import logging

from src.domain.project import analyze, analyze_normalized


def calculate(rows, settings, *, normalized: bool = False, corrections=None):
    try:
        if normalized:
            result = analyze_normalized(rows, settings, corrections)
        else:
            result = analyze(rows, settings, corrections)
        json.dumps(result, ensure_ascii=False, allow_nan=False)
    except ValueError as error:
        return None, str(error)
    except Exception:
        logging.getLogger(__name__).exception('计算运行失败')
        return None, '计算遇到内部错误，请检查台账后重试。'
    return result, None
