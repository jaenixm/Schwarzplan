import flet as ft
import flet_map as ftm

def main(page: ft.Page):
    ref = ft.Ref[ftm.MarkerLayer]()
    
    def tap(e):
        print(e.coordinates.latitude, e.coordinates.longitude)
        ref.current.markers = [ftm.Marker(
            coordinates=e.coordinates,
            content=ft.Icon(ft.Icons.LOCATION_ON, color="red")
        )]
        ref.current.update()
        
    m = ftm.Map(
        expand=True,
        on_tap=tap,
        layers=[
            ftm.TileLayer(url_template="https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png"),
            ftm.MarkerLayer(ref=ref, markers=[])
        ]
    )
    page.add(m)

ft.run(main)
