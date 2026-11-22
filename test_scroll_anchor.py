#!/usr/bin/env python
# Headless self-check for the games list scroll anchoring, needs imgui but opens no window.
# Runs the real sync_scroll()/track_scroll_anchor() from modules/gui.py against an offscreen table.
# Run: python test_scroll_anchor.py
# Or in a frozen build, from the repo root:
#   F95Checker-Debug.exe -c "exec(open('test_scroll_anchor.py').read(), {})"
import ast
import pathlib

import imgui

GUI = pathlib.Path(globals().get("__file__", "test_scroll_anchor.py")).resolve().parent / "modules/gui.py"
WANTED = ("sync_scroll", "track_scroll_anchor")


def load_methods():
    tree = ast.parse(GUI.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "MainGUI")
    funcs = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in WANTED]
    assert len(funcs) == len(WANTED), "methods not found in modules/gui.py"
    ns = {"imgui": imgui}
    exec(compile(ast.Module(body=funcs, type_ignores=[]), str(GUI), "exec"), ns)
    return type("FakeGUI", (), {name: ns[name] for name in WANTED})


FakeGUI = load_methods()
imgui.create_context()
io = imgui.get_io()
io.display_size = (1280, 720)
io.delta_time = 1 / 60
io.fonts.get_tex_data_as_rgba32()


def new_gui(ids):
    gui = FakeGUI()
    gui.current_tab = None
    gui.show_games_ids = {None: ids}
    gui.scroll_anchors = []
    gui.restore_anchor = None
    gui.reordered = False
    gui.scroll_to_top = False
    gui.switched_display_mode = False
    gui.scroll_percent = 0.0
    return gui


def frame(gui, set_scroll=None):
    # Same shape as draw_games_list(): frozen header, culled rows replaced by dummies
    imgui.new_frame()
    imgui.set_next_window_position(0, 0)
    imgui.set_next_window_size(*io.display_size)
    imgui.begin("main", flags=imgui.WINDOW_NO_DECORATION)
    rows = {}  # Visible id -> screen y, in list order
    if imgui.begin_table("list", 2, flags=imgui.TABLE_SCROLL_Y):
        imgui.table_setup_column("a")
        imgui.table_setup_column("b")
        imgui.table_setup_scroll_freeze(0, 1)
        imgui.table_headers_row()
        gui.sync_scroll()
        if set_scroll is not None:
            imgui.set_scroll_y(set_scroll)
        height = imgui.get_frame_height()
        for id in gui.show_games_ids[None]:
            imgui.table_next_row()
            imgui.table_set_column_index(0)
            gui.track_scroll_anchor(id, visible := imgui.is_rect_visible(io.display_size.x, height))
            if not visible:
                imgui.dummy(0, height)
                continue
            rows[id] = imgui.get_cursor_screen_pos().y
            imgui.button("", width=imgui.FLOAT_MIN)
        scroll = imgui.get_scroll_y()
        imgui.end_table()
    imgui.end()
    imgui.render()
    return rows, scroll


def settle(gui, set_scroll=None):
    frame(gui, set_scroll)
    for _ in range(3):
        result = frame(gui)
    return result


def move(start_scroll, nth_visible, to):
    # Scroll somewhere, move one on-screen game like a recheck does
    ids = list(range(300))
    gui = new_gui(ids)
    settle(gui)
    rows, scroll = settle(gui, start_scroll)
    moved = list(rows)[nth_visible]
    new_ids = [id for id in ids if id != moved]
    new_ids.insert(to, moved)
    gui.show_games_ids = {None: new_ids}
    gui.reordered = True  # What calculate_ids() sets on a pure reorder
    new_rows, new_scroll = settle(gui)
    return moved, rows, scroll, new_rows, new_scroll


def assert_view_kept(moved, rows, new_rows):
    # The first on-screen game that did not move is still where it was, so the view did not jump
    stayed = next(id for id in rows if id != moved)
    assert new_rows.get(stayed) == rows[stayed], f"moving game {moved} dragged the view: game {stayed} {rows[stayed]} -> {new_rows.get(stayed)}"


def test_other_game_moves_up():
    moved, rows, _, new_rows, _ = move(1000.0, 3, 0)
    assert_view_kept(moved, rows, new_rows)


def test_other_game_moves_down():
    moved, rows, _, new_rows, _ = move(1000.0, 3, 250)
    assert_view_kept(moved, rows, new_rows)


def test_top_row_itself_moves_up():
    # The rechecked game was the top visible row: the view must not follow it to its new place
    moved, rows, _, new_rows, _ = move(1000.0, 0, 0)
    assert_view_kept(moved, rows, new_rows)


def test_top_row_itself_moves_down():
    moved, rows, _, new_rows, _ = move(1000.0, 0, 250)
    assert_view_kept(moved, rows, new_rows)


def test_at_top_stays_at_top():
    # Scrolled to the very top, a game sorting first shows up there instead of pushing the list down
    moved, _, _, new_rows, new_scroll = move(0.0, 5, 0)
    assert new_scroll == 0.0 and list(new_rows)[0] == moved, (moved, list(new_rows)[:3], new_scroll)


def test_at_top_first_game_moves_down():
    moved, _, _, new_rows, new_scroll = move(0.0, 0, 250)
    assert new_scroll == 0.0 and moved not in new_rows, (moved, list(new_rows)[:3], new_scroll)


if __name__ == "__main__" or "__file__" not in globals():
    for name, test in list(globals().items()):
        if name.startswith("test_") and callable(test):
            test()
            print(f"ok  {name}")
    print("all passed")
