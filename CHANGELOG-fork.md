### Fixed:
- F95Checker 11.4.19 on Windows failed at startup with "cx_Freeze: Python error in main script", an ImportError about winrt not reading its projection table. The build had picked up version 4.0 of the winrt package that Windows notifications use, and it cannot run from inside the packaged app. It is pinned back to 3.2.1, the version 11.4.18 shipped with
