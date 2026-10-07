"""Проверка плавной анимации текста песни: выплывает снизу, ползёт вверх, тает у краёв.

Запуск:  python test_lyrics_scroll.py     (код возврата 0 — все проверки прошли)
"""
import os, sys, traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer, QEventLoop

import main


class _Tee:
    """Дублирует вывод в консоль и в файл, чтобы результат теста не потерялся."""

    def __init__(self, path):
        self._fh = open(path, "w", encoding="utf-8")

    def write(self, text):
        sys.__stdout__.write(text)
        self._fh.write(text)
        self._fh.flush()

    def flush(self):
        sys.__stdout__.flush()
        self._fh.flush()


results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))
    print(("PASS " if condition else "FAIL ") + name + (f"  -> {detail}" if detail else ""))


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def main_test():
    app = QApplication(sys.argv)
    w = main.LyricsMarquee(4)
    w.resize(360, w._total_height())
    w.show()

    # --- 1. Настройки анимации ------------------------------------------------
    check("строка обновляется часто (плавно, а не рывками)", w._timer.interval() <= 40,
          f"{w._timer.interval()} мс")
    check("одна строка идёт наверх 3–7 секунд (успевают прочитать)",
          3.0 <= w._line_sec <= 7.0, f"{w._line_sec} с")
    check("выплывание снизу длится не дольше 2 секунд",
          0.4 <= w._intro_sec <= 2.0, f"{w._intro_sec} с")
    check("высота блока вмещает зону «таяния» снизу и сверху",
          w.minimumHeight() >= w._total_height() and w._margin() > 0,
          f"min={w.minimumHeight()} total={w._total_height()} margin={w._margin()}")

    # --- 2. Текст начинает снизу и уплывает наверх ----------------------------
    long_text = " ".join(f"строка номер {i} текста песни" for i in range(1, 25))
    w.set_scroll_text(long_text)
    check("текст принят", w._raw_text == long_text)
    check("строк после переноса больше, чем видно", len(w._all_lines) > w.visible_lines,
          f"{len(w._all_lines)} строк, видно {w.visible_lines}")
    check("сразу после загрузки текст НИЖЕ рабочего места (выплывает снизу)",
          w._scroll_offset() < 0, f"сдвиг {w._scroll_offset():.1f} px")

    waited = 0
    while w._intro > 0 and waited < 3000:
        wait(100)
        waited += 100
    check("вступление дошло до конца", w._intro == 0.0, f"остаток {w._intro:.3f}")
    check("после вступления текст на своём месте", abs(w._scroll_offset()) < 0.5,
          f"сдвиг {w._scroll_offset():.2f} px")

    # --- 3. Непрерывный ход вверх --------------------------------------------
    before = (w._start, w._scroll_offset())
    wait(1200)
    after = (w._start, w._scroll_offset())
    moved = (after[0] * w._line_h + after[1]) - (before[0] * w._line_h + before[1])
    check("текст плавно ползёт вверх (позиция растёт)", moved > 1.0, f"сдвинулось {moved:.1f} px")
    check("шаг за шагом без скачков на целую строку",
          after[0] - before[0] in (0, 1) and after[0] >= before[0],
          f"строка {before[0]} -> {after[0]}")

    # --- 4. Строки уходят наверх и текст закольцовывается --------------------
    fast = main.LyricsMarquee(2)
    fast.resize(400, fast._total_height())
    fast._line_sec = 0.05                      # ускоряем, чтобы увидеть много переходов
    fast.set_scroll_text("раз два три четыре пять шесть семь восемь девять десять")
    start_seen, ok_inv, prev = set(), True, fast._start
    for _ in range(60):
        fast._animate()
        if fast._start != prev:
            start_seen.add(fast._start)
            prev = fast._start
        if not 0 <= fast._start < len(fast._all_lines):
            ok_inv = False
    check("индекс верхней строки всегда в диапазоне (нет выхода за список)", ok_inv,
          f"строк {len(fast._all_lines)}")
    check("строки уходят наверх и индекс прокручивается", len(start_seen) >= 3,
          f"позиции {sorted(start_seen)}")
    check("список строк не ломается при прокрутке",
          fast._raw_text == "раз два три четыре пять шесть семь восемь девять десять"
          and len(fast._all_lines) > 0, f"{len(fast._all_lines)} строк")

    # --- 5. Прозрачность: тает у верхнего и нижнего края ----------------------
    top, bottom = w._area_top(), w._area_bottom()
    half = w._line_h / 2.0
    a_center = w._alpha_for(top + w._line_h)
    a_inside_top = w._alpha_for(top)                  # строка только что въехала в блок
    a_mid_top = w._alpha_for(top - half)              # её центр ровно на краю
    a_gone = w._alpha_for(top - w._margin() - half)   # уплыла за край
    a_inside_bottom = w._alpha_for(bottom - w._line_h)
    a_below = w._alpha_for(bottom)                    # выплывает снизу
    check("в центре блока текст полностью непрозрачен", a_center > 0.99, f"alpha={a_center:.2f}")
    check("строка, въехавшая в блок, читается полностью", a_inside_top > 0.99,
          f"alpha={a_inside_top:.2f}")
    check("строка наполовину уплыла — она наполовину бледная",
          0.15 < a_mid_top < 0.95, f"alpha={a_mid_top:.2f}")
    check("выше блока текст полностью исчезает", a_gone == 0.0, f"alpha={a_gone:.2f}")
    check("нижний край внутри блока непрозрачен", a_inside_bottom > 0.99,
          f"alpha={a_inside_bottom:.2f}")
    check("ниже блока текст бледный (выплывает снизу)", a_below < 0.25, f"alpha={a_below:.2f}")
    # Полоса таяния — это промежуток от края виджета (y=0) до верха блока.
    # Именно здесь alpha идёт от 0 к 1; внутри блока текст уже непрозрачен.
    steps = 11
    alphas = [w._alpha_for(i * top / steps) for i in range(steps + 1)]
    check("прозрачность растёт от 0 у края окна до 1 в блоке, без ступенек",
          all(alphas[i] <= alphas[i + 1] for i in range(len(alphas) - 1))
          and alphas[0] == 0.0 and alphas[-1] > 0.99 and 0.05 < alphas[5] < 0.95,
          " -> ".join(f"{a:.2f}" for a in alphas))
    check("текст гаснет не позже верхнего края окна (нет резкого обреза)",
          w._alpha_for(0.0) == 0.0 and w._alpha_for(w.height()) == 0.0,
          f"верх={w._alpha_for(0.0):.2f} низ={w._alpha_for(w.height()):.2f}")

    # --- 6. Отрисовка не падает на краях и пустом тексте ----------------------
    errors = []
    for stage in ("ok", "empty", "short", "huge"):
        try:
            if stage == "empty":
                w.set_scroll_text("")
            elif stage == "short":
                w.set_scroll_text("Одна короткая строчка")
            elif stage == "huge":
                w.set_scroll_text("слово " * 400)
            w.resize(700 if stage == "huge" else 360, w._total_height())
            w.repaint()
        except Exception as exc:
            errors.append(f"{stage}: {exc}")
    check("отрисовка без исключений (пусто/коротко/длинно/узко)", not errors, str(errors))
    check("пустой текст не ломает анимацию", not w._all_lines or all(w._all_lines))

    # --- 7. Длинная строка бежит по горизонтали --------------------------------
    from PyQt6.QtGui import QFontMetrics
    w.resize(360, w._total_height())        # сначала ширина, потом текст: перенос верный
    long_line = "Очень длинная строка которая точно не влезет в ширину блока титров"
    w.set_scroll_text(long_line)
    fm = QFontMetrics(w._font)
    avail = w.width() - 2 * w._pad
    check("длинная строка не влезает в блок (иначе проверка бессмысленна)",
          fm.horizontalAdvance(long_line) > avail,
          f"{fm.horizontalAdvance(long_line)} px > {avail} px")
    first = w._fit_line(long_line, fm, avail, 0)
    for _ in range(30):
        w._tick += 1
    second = w._fit_line(long_line, fm, avail, 0)
    check("длинная строка прокручивается по горизонтали", first != second and len(first) > 0,
          f"{len(first)} симв. -> {len(second)} симв.")
    check("показанный кусок помещается в ширину блока",
          fm.horizontalAdvance(first) <= avail, f"{fm.horizontalAdvance(first)} <= {avail} px")

    # --- 8. Новый трек снова выплывает снизу ---------------------------------
    w.set_scroll_text("другой трек")
    check("при смене текста анимация начинается заново снизу",
          w._intro == 1.0 and w._start == 0, f"intro={w._intro}")

    w.close()
    fast.close()
    return True


if __name__ == "__main__":
    sys.stdout = _Tee(os.path.join(os.path.dirname(os.path.abspath(__file__)), "lyrics_scroll.log"))
    ok = False
    try:
        ok = main_test()
    except Exception:
        main.console("ТЕСТ УПАЛ С ИСКЛЮЧЕНИЕМ")
        main.console(traceback.format_exc())
    passed = sum(1 for _, good, _ in results if good)
    main.console("")
    main.console(f"Итог: {passed}/{len(results)} проверок пройдено")
    main.console(f"EXIT={0 if (ok and passed == len(results)) else 1}")
    sys.exit(0 if (ok and passed == len(results)) else 1)
