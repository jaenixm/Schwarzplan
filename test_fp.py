import flet as ft
def main(page: ft.Page):
    fp = ft.FilePicker()
    def clk(e):
        fp.save_file()
    page.add(ft.ElevatedButton("Test", on_click=clk))
ft.run(main)
