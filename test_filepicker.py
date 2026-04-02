import flet as ft
def main(page: ft.Page):
    fp = ft.FilePicker()
    print("Page associated?", fp.page is not None)
ft.run(main)
