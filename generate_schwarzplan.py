import osmnx as ox
import matplotlib.pyplot as plt
from shapely.geometry import Point
import geopandas as gpd

def create_schwarzplan_by_bbox(bbox, output_filename="schwarzplan.pdf"):
    """
    Fetches buildings for a given bounding box and creates a figure-ground diagram (Schwarzplan).
    
    Args:
        bbox (tuple): Bounding box in the format (north, south, east, west).
        output_filename (str): Path to save the resulting PDF.
    """
    print(f"Fetching buildings for bbox region: N:{bbox[0]}, S:{bbox[1]}, E:{bbox[2]}, W:{bbox[3]}...")
    
    # Define tags to fetch only buildings
    tags = {'building': True}
    
    try:
        # Fetch features within the bounding box (OSMnx 2.0+)
        # OSMnx 2.0+ features_from_bbox expects bbox=(left, bottom, right, top) i.e. (west, south, east, north)
        north, south, east, west = bbox
        gdf_buildings = ox.features_from_bbox(bbox=(west, south, east, north), tags=tags)
    except Exception:
        # Fallback for older OSMnx versions or different parameter signature
        north, south, east, west = bbox
        try:
            gdf_buildings = ox.geometries_from_bbox(north, south, east, west, tags=tags)
        except Exception:
            gdf_buildings = ox.features_from_bbox(north, south, east, west, tags=tags)

    if gdf_buildings.empty:
        print("No buildings found in the specified bounding box.")
        return

    print("Generating Schwarzplan...")
    
    # Set up the plot with a white background
    fig, ax = plt.subplots(figsize=(10, 10))
    fig.patch.set_facecolor('white') 
    ax.set_facecolor('white')
    
    # Plot the buildings in solid black
    gdf_buildings.plot(ax=ax, facecolor='black', edgecolor='none')
    
    # Remove margins and axis for a clean map look
    plt.margins(0)
    ax.set_axis_off()
    
    print(f"Saving to {output_filename}...")
    # Save the output figure as a PDF
    plt.savefig(output_filename, format='pdf', bbox_inches='tight', pad_inches=0, dpi=300)
    print("Export complete!")

def create_schwarzplan_a3_landscape(center_lat_lon, scale=5000, output_filename="schwarzplan_a3.pdf"):
    """
    Creates an exact-scale A3 landscape Schwarzplan for a given center point and scale.
    
    Args:
        center_lat_lon (tuple): (latitude, longitude) center point
        scale (int): The scale denominator (e.g., 5000 for 1:5000 scale)
        output_filename (str): Path to save the resulting PDF.
    """
    # A3 paper dimensions in meters (Landscape)
    paper_width_m = 0.420
    paper_height_m = 0.297
    
    # 15mm white space (border) from all sides
    margin_m = 0.015
    
    # Map area dimensions after removing the borders
    map_width_m = paper_width_m - 2 * margin_m
    map_height_m = paper_height_m - 2 * margin_m
    
    # Real world dimensions in meters that fit exactly in the map area at the given scale
    real_width_m = map_width_m * scale
    real_height_m = map_height_m * scale
    
    # Buffer the download size slightly to make sure polygons on the edges are fetched completely
    max_radius = (max(real_width_m, real_height_m) / 2) * 1.1
    
    print(f"Fetching buildings for center {center_lat_lon} with ~{max_radius:.2f}m radius (scale 1:{scale})...")
    tags = {'building': True}
    
    try:
        # OSMnx 2.0+
        gdf_buildings = ox.features_from_point(center_lat_lon, tags=tags, dist=max_radius)
    except Exception:
        # Older OSMnx
        try:
            gdf_buildings = ox.geometries_from_point(center_lat_lon, tags=tags, dist=max_radius)
        except Exception:
            gdf_buildings = ox.features_from_point(center_lat_lon, tags=tags, dist=max_radius)
            
    if gdf_buildings.empty:
        print("No buildings found in the specified area.")
        return
        
    # Filter to only polygon geometries (exclude point/line features that render as blue dots)
    gdf_buildings = gdf_buildings[gdf_buildings.geometry.type.isin(['Polygon', 'MultiPolygon'])]
    
    if gdf_buildings.empty:
        print("No polygon buildings found in the specified area.")
        return
    
    print("Projecting data to UTM for exact spatial scaling...")
    # Project to a local UTM zone so coordinates are represented cleanly in standard meters
    target_crs = gdf_buildings.estimate_utm_crs()
    gdf_proj = gdf_buildings.to_crs(target_crs)
    
    # Project the center point to the same CRS to know where to plot
    # Point uses (lon, lat) internally
    center_lon, center_lat = center_lat_lon[1], center_lat_lon[0]
    center_pt = gpd.GeoSeries([Point(center_lon, center_lat)], crs="EPSG:4326")
    center_pt_proj = center_pt.to_crs(gdf_proj.crs)
    
    center_x = center_pt_proj.geometry.x.iloc[0]
    center_y = center_pt_proj.geometry.y.iloc[0]
    
    print(f"Generating exact A3 landscape map at 1:{scale} scale with 15mm border...")
    # Set the precise figure size in inches for Matplotlib
    fig_width_in = paper_width_m * 1000 / 25.4
    fig_height_in = paper_height_m * 1000 / 25.4
    
    fig, ax = plt.subplots(figsize=(fig_width_in, fig_height_in))
    fig.patch.set_facecolor('white') 
    ax.set_facecolor('white')
    
    # Plot the buildings
    gdf_proj.plot(ax=ax, facecolor='black', edgecolor='none')
    
    # Set spatial limit window using meters based on target scale and center coordinate
    ax.set_xlim([center_x - real_width_m / 2, center_x + real_width_m / 2])
    ax.set_ylim([center_y - real_height_m / 2, center_y + real_height_m / 2])
    
    # Ensure aspect ratio is completely equal true-to-geometry representation
    ax.set_aspect('equal')
    
    # Strip any extra UI framing from matplotlib axes
    ax.set_axis_off()
    
    # Adjust axes perfectly nicely to the very bounds of the figure considering the physical 15mm margin
    # Matplotlib subplots_adjust uses normalized coordinates (0 to 1) 
    plt.subplots_adjust(
        left=margin_m / paper_width_m, 
        right=(paper_width_m - margin_m) / paper_width_m, 
        bottom=margin_m / paper_height_m, 
        top=(paper_height_m - margin_m) / paper_height_m
    )
    
    print(f"Saving strictly proportional map to {output_filename}...")
    # Do NOT use bbox_inches='tight' since it recalculates figure size and breaks the exact 1:scale physical sizing constraint
    plt.savefig(output_filename, format='pdf', pad_inches=0, dpi=300)
    print("Export complete!")

if __name__ == "__main__":
    # Example usage using the new mode:
    # A point in Oslo: (latitude, longitude)
    center_oslo = (53.55814802475552, 9.96321479975538)
    
    # Generate A3 landscape pdf mapping exactly 1:5000 from center
    create_schwarzplan_a3_landscape(center_oslo, scale=1000, output_filename="schwarzplan_a3_hamburg_1_1000.pdf")
