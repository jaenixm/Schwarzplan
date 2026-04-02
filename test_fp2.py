import flet as ft
def main(page: ft.Page):
    fp = ft.FilePicker()
    def clk(e):
        try:
            fp.save_file()
            print("save_file OK")
        except Exception as e:
            print("save_file error:", e)
    btn = ft.ElevatedButton("Test", on_click=clk)
    page.add(btn)
    clk(None)
ft.run(main)
