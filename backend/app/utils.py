import os
import re
import math
from datetime import datetime
from typing import Optional, Tuple, List, Callable, Dict, Any
from lxml import etree


def _create_secure_xml_parser() -> etree.XMLParser:
    """
    Build a hardened XML parser:
    - disable entity resolution / DTD loading
    - block network access
    - keep strict parsing (no recovery mode)
    """
    return etree.XMLParser(
        resolve_entities=False,
        load_dtd=False,
        no_network=True,
        huge_tree=False,
        recover=False,
    )


def _ordered_closed_polygon(points: List[Tuple[float, float]]) -> Optional[List[Tuple[float, float]]]:
    unique: List[Tuple[float, float]] = []
    for point in points or []:
        try:
            lon = float(point[0])
            lat = float(point[1])
        except (TypeError, ValueError, IndexError):
            continue
        current = (lon, lat)
        if unique and abs(unique[-1][0] - lon) < 1e-12 and abs(unique[-1][1] - lat) < 1e-12:
            continue
        if unique and abs(unique[0][0] - lon) < 1e-12 and abs(unique[0][1] - lat) < 1e-12:
            continue
        if current not in unique:
            unique.append(current)
    if len(unique) < 3:
        return None
    if len(unique) == 4:
        center_lon = sum(item[0] for item in unique) / len(unique)
        center_lat = sum(item[1] for item in unique) / len(unique)
        ordered = sorted(
            unique,
            key=lambda item: math.atan2(item[1] - center_lat, item[0] - center_lon),
        )
    else:
        ordered = unique
    if ordered[0] != ordered[-1]:
        ordered.append(ordered[0])
    return ordered


def _closed_polygon_if_valid(points: List[Tuple[float, float]]) -> Optional[List[Tuple[float, float]]]:
    ring = [(float(point[0]), float(point[1])) for point in points or []]
    if len(ring) < 3:
        return None
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring


def _ordered_closed_polygon_from_corner_details(corner_details: Dict[str, Dict[str, Any]]) -> Optional[List[Tuple[float, float]]]:
    by_name = {str(key or "").strip().lower(): value for key, value in (corner_details or {}).items()}
    name_order = ["bottomleft", "bottomright", "topright", "topleft"]
    if all(name in by_name for name in name_order):
        return _closed_polygon_if_valid([(by_name[name]["lon"], by_name[name]["lat"]) for name in name_order])

    entries = [
        value
        for value in (corner_details or {}).values()
        if value.get("lon") is not None
        and value.get("lat") is not None
        and value.get("ref_row") is not None
        and value.get("ref_col") is not None
    ]
    if len(entries) >= 4:
        min_row = min(float(item["ref_row"]) for item in entries)
        max_row = max(float(item["ref_row"]) for item in entries)
        min_col = min(float(item["ref_col"]) for item in entries)
        max_col = max(float(item["ref_col"]) for item in entries)
        targets = [(min_row, min_col), (min_row, max_col), (max_row, max_col), (max_row, min_col)]
        remaining = list(entries)
        ordered_entries: List[Dict[str, Any]] = []
        for target_row, target_col in targets:
            chosen = min(
                remaining,
                key=lambda item: abs(float(item["ref_row"]) - target_row) + abs(float(item["ref_col"]) - target_col),
            )
            ordered_entries.append(chosen)
            remaining.remove(chosen)
        return _closed_polygon_if_valid([(item["lon"], item["lat"]) for item in ordered_entries])

    return _ordered_closed_polygon([(value["lon"], value["lat"]) for value in (corner_details or {}).values()])


def build_corner_pixel_mapping(corner_details: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if len(corner_details or {}) < 4:
        return None

    entries: List[Tuple[str, Dict[str, Any]]] = []
    ref_rows = []
    ref_cols = []
    for name, info in (corner_details or {}).items():
        if info.get("lon") is None or info.get("lat") is None:
            return None
        if info.get("ref_row") is None or info.get("ref_col") is None:
            return None
        try:
            ref_row = int(float(info["ref_row"]))
            ref_col = int(float(info["ref_col"]))
        except (TypeError, ValueError):
            return None
        normalized = dict(info)
        normalized["ref_row"] = ref_row
        normalized["ref_col"] = ref_col
        entries.append((str(name), normalized))
        ref_rows.append(ref_row)
        ref_cols.append(ref_col)

    min_row, max_row = min(ref_rows), max(ref_rows)
    min_col, max_col = min(ref_cols), max(ref_cols)
    remaining = entries[:]

    def pick(target_row: int, target_col: int) -> Optional[Tuple[float, float]]:
        if not remaining:
            return None
        ranked = []
        for index, (name, info) in enumerate(remaining):
            row = int(info["ref_row"])
            col = int(info["ref_col"])
            score = abs(row - target_row) + abs(col - target_col)
            ranked.append((score, abs(row - target_row), abs(col - target_col), name, index))
        ranked.sort()
        chosen_index = ranked[0][4]
        _, chosen = remaining.pop(chosen_index)
        try:
            return float(chosen["lon"]), float(chosen["lat"])
        except (TypeError, ValueError):
            return None

    top_left = pick(min_row, min_col)
    top_right = pick(min_row, max_col)
    bottom_left = pick(max_row, min_col)
    bottom_right = pick(max_row, max_col)

    if not all([top_left, top_right, bottom_left, bottom_right]):
        return None

    return {
        "top_left": [top_left[0], top_left[1]],
        "top_right": [top_right[0], top_right[1]],
        "bottom_left": [bottom_left[0], bottom_left[1]],
        "bottom_right": [bottom_right[0], bottom_right[1]],
        "source": "xml_ref_row_col",
    }

# --- Sentinel-1 (S1A/S1B/S1C) Parsers ---

def _radar_meta_base() -> Dict[str, Any]:
    return {
        "satellite": None,
        "imaging_date": None,
        "imaging_mode": None,
        "polarization": None,
        "satellite_mode": None,
        "receiving_station": None,
        "orbit_circle": None,
        "scene_center_lon": None,
        "scene_center_lat": None,
        "acquisition_time_utc": None,
        "product_type": None,
        "source_product_token": None,
        "image_data_type": None,
        "image_data_format": None,
        "product_variant": None,
        "product_level": None,
        "product_unique_id": None,
        "satellite_family": None,
        "look_direction": None,
        "geocoded_flag": None,
    }


def _extract_date_yyyymmdd(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = value.strip()
    if len(value) < 8:
        return None
    # Find first 8-digit sequence
    for idx in range(0, len(value) - 7):
        chunk = value[idx:idx + 8]
        if chunk.isdigit():
            return chunk
    return None


def _parse_coord_token(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    token = value.strip()
    if not token:
        return None
    sign = 1.0
    head = token[0].upper()
    if head in ("E", "W", "N", "S"):
        sign = -1.0 if head in ("W", "S") else 1.0
        token = token[1:]
    try:
        return float(token) * sign
    except ValueError:
        return None


def normalize_satellite_family(value: Optional[str]) -> Optional[str]:
    raw = str(value or "").strip().upper()
    if not raw:
        return None
    compact = raw.replace("-", "").replace("_", "").replace(" ", "")
    if compact in {"LT1", "LT1A", "LT1B", "LUTAN1", "LUTAN1A", "LUTAN1B"}:
        return "LT1"
    if compact in {"S1", "S1A", "S1B", "S1C", "SENTINEL1", "SENTINEL1A", "SENTINEL1B", "SENTINEL1C"}:
        return "S1"
    if compact in {"GF3", "GAOFEN3"}:
        return "GF3"
    return raw


def parse_s1_radar_filename(folder_name: str) -> Optional[Dict[str, Any]]:
    """
    Parses key info from a Sentinel-1 radar data folder name.
    Example: S1A_IW_SLC__1SDV_20250101T104105_...
    Returns a metadata dict.
    """
    name = os.path.basename(str(folder_name or "").strip())
    if name.lower().endswith(".zip"):
        name = name[:-4]
    if name.lower().endswith(".safe"):
        name = name[:-5]
    match = re.match(
        r"^(?P<satellite>S1[A-Z])_"
        r"(?P<mode>[A-Z0-9]+)_"
        r"(?P<product>[A-Z0-9]+)_+"
        r"(?P<class>[0-9A-Z]{4})_"
        r"(?P<start>\d{8}T\d{6}(?:\.\d+)?)_"
        r"(?P<stop>\d{8}T\d{6}(?:\.\d+)?)_"
        r"(?P<absolute_orbit>\d+)_"
        r"(?P<datatake>[0-9A-F]+)_"
        r"(?P<product_uid>[0-9A-F]+)$",
        name,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    meta = _radar_meta_base()
    meta["satellite"] = match.group("satellite").upper()
    meta["satellite_family"] = normalize_satellite_family(meta["satellite"])
    meta["imaging_date"] = match.group("start")[:8]
    meta["imaging_mode"] = match.group("mode").upper()
    meta["source_product_token"] = match.group("class").upper()
    meta["product_type"] = match.group("product").upper()
    meta["product_level"] = "L1"
    polarization = match.group("class").upper()  # e.g. 1SDV -> DV
    meta["polarization"] = polarization[-2:] if len(polarization) > 2 else polarization
    meta["orbit_circle"] = match.group("absolute_orbit").lstrip("0") or match.group("absolute_orbit")
    meta["product_unique_id"] = name
    try:
        start_time = datetime.strptime(match.group("start").split(".")[0], "%Y%m%dT%H%M%S")
        meta["acquisition_time_utc"] = start_time.isoformat()
    except ValueError:
        meta["acquisition_time_utc"] = match.group("start")
    return meta


def parse_s1_orbit_filename(file_name: str) -> Optional[Tuple[str, str]]:
    """
    Parses key info from a Sentinel-1 orbit file name.
    Example: S1A_OPER_AUX_POEORB_OPOD_20250121T120000_..._V20250101_...EOF
    Returns: (satellite, date)
    """
    parts = file_name.split('_')
    if len(parts) < 8 or not parts[0].startswith('S1'):
        return None
        
    satellite = parts[0]
    # Find the validation date part, e.g., V20250101
    date_part = next((p for p in parts if p.startswith('V20')), None)
    if not date_part or len(date_part) < 9:
        return None
    
    return satellite, date_part[1:9]


# --- Land-Viewer (LT1) Parsers ---

def parse_lt1_radar_filename(folder_name: str) -> Optional[Dict[str, Any]]:
    """
    Parses key info from a Land-Viewer (LT1) radar data folder name.
    Example: LT1B_MONO_SYC_STRIP1_018153_E135.4_N48.3_20250701_SLC_HH_S2A_0000790171
    Returns a metadata dict.
    """
    parts = folder_name.split('_')
    if len(parts) < 10 or not parts[0].startswith('LT1'):
        return None

    meta = _radar_meta_base()
    meta["satellite"] = parts[0]
    meta["satellite_family"] = normalize_satellite_family(parts[0])
    if len(parts) > 1:
        meta["satellite_mode"] = parts[1]
    if len(parts) > 2:
        meta["receiving_station"] = parts[2]
    if len(parts) > 3:
        meta["imaging_mode"] = parts[3]
    if len(parts) > 4:
        meta["orbit_circle"] = parts[4]
    if len(parts) > 5:
        meta["scene_center_lon"] = _parse_coord_token(parts[5])
    if len(parts) > 6:
        meta["scene_center_lat"] = _parse_coord_token(parts[6])
    if len(parts) > 7:
        meta["imaging_date"] = _extract_date_yyyymmdd(parts[7])
        meta["acquisition_time_utc"] = parts[7]
    if len(parts) > 8:
        meta["source_product_token"] = parts[8]
        meta["product_type"] = parts[8]
    if len(parts) > 9:
        meta["polarization"] = parts[9]
    if len(parts) > 10:
        meta["product_level"] = parts[10]
    if len(parts) > 11:
        meta["product_unique_id"] = parts[11]

    return meta


def parse_lt1_orbit_filename(file_name: str) -> Optional[Tuple[str, str]]:
    """
    Parses key info from a Land-Viewer (LT1) GPS data file name.
    Example: LT1B_GpsData_GAS_C_20250701.txt
    Returns: (satellite, date)
    """
    parts = file_name.split('_')
    if len(parts) < 4 or not file_name.endswith('.txt') or not parts[0].startswith('LT1'):
        return None
        
    satellite = parts[0]
    date_str = parts[-1].split('.')[0]
    return satellite, date_str


# --- GF3 (GaoFen-3) Parsers ---

def parse_gf3_l2_dirname(folder_name: str) -> Optional[Dict[str, Any]]:
    """
    Parses key info from a GF3 L2 output directory name.
    Supports two naming patterns:
    1. Our pipeline output: gf3_GF3_SAR_<...>_<date>_<...> (prefixed with gf3_)
    2. Raw GF3 product name: GF3_SAR_<mode>_<pol>_<orbit>_<date>_<...>
    Example: GF3_SAR_UFS_HH_011234_E120.5_N31.2_20230615_L1A_HH_L10003456789
    """
    name = folder_name
    # Strip our pipeline prefix
    if name.startswith("gf3_"):
        name = name[4:]
    if not name.startswith("GF3"):
        return None

    meta = _radar_meta_base()
    meta["satellite"] = "GF3"
    meta["satellite_family"] = normalize_satellite_family("GF3")

    parts = name.split("_")
    # Try to extract date: first 8-digit segment
    for part in parts:
        date = _extract_date_yyyymmdd(part)
        if date:
            meta["imaging_date"] = date
            break

    # Try to extract polarization
    for pol in ("HH", "HV", "VH", "VV"):
        if pol in parts:
            meta["polarization"] = pol
            break

    # Try to extract imaging mode (3rd part for standard GF3 naming)
    if len(parts) >= 3:
        mode = parts[2]  # e.g. UFS, FSI, QPSI
        if mode.isalpha() and len(mode) <= 6:
            meta["imaging_mode"] = mode

    # Try to extract coordinates
    for part in parts:
        if part.startswith("E") or part.startswith("W"):
            meta["scene_center_lon"] = _parse_coord_token(part)
        elif part.startswith("N") or part.startswith("S"):
            meta["scene_center_lat"] = _parse_coord_token(part)

    return meta


# --- Generic Parsers Dispatcher ---

# List of available radar and orbit parsers
RADAR_PARSERS: List[Callable[[str], Optional[Dict[str, Any]]]] = [
    parse_lt1_radar_filename,
    parse_s1_radar_filename,
    parse_gf3_l2_dirname,
]

ORBIT_PARSERS: List[Callable[[str], Optional[Tuple[str, str]]]] = [
    parse_lt1_orbit_filename,
    parse_s1_orbit_filename,
]

def get_parser(filename: str, parsers: list) -> Optional[tuple]:
    """Tries a list of parsers on a filename and returns the first success."""
    for parser in parsers:
        result = parser(filename)
        if result:
            return result
    return None


# --- XML and File Utilities ---

def find_xml_file(directory: str) -> Optional[str]:
    """
    Finds the most relevant XML file in a given directory.
    It prioritizes files ending with '.meta.xml'.
    """
    try:
        xml_files = [f for f in os.listdir(directory) if f.lower().endswith('.xml')]
    except FileNotFoundError:
        return None  # Directory might not exist if parsing fails early

    if not xml_files:
        return None

    # Prioritize '.meta.xml'
    for file in xml_files:
        if file.lower().endswith('.meta.xml'):
            return os.path.join(directory, file)

    # Fallback: if only one XML file exists, use it.
    if len(xml_files) == 1:
        return os.path.join(directory, xml_files[0])

    # Ambiguous case: multiple XMLs, none are '.meta.xml'.
    print(f"Warning: Multiple XML files found in {directory}, none is '.meta.xml'. Using first one: {xml_files[0]}")
    return os.path.join(directory, xml_files[0])


def parse_xml_metadata(
    xml_file_path: str
) -> Optional[Tuple[Optional[List[Tuple[float, float]]], Optional[Dict[str, Any]]]]:
    """
    Parses metadata from a radar data XML file.
    Returns a tuple containing:
    - A list of corner coordinates (polygon).
    - A metadata dict (orbit direction, imaging mode, polarization, etc).
    """
    xml_name = os.path.basename(xml_file_path or "")
    try:
        parser = _create_secure_xml_parser()
        tree = etree.parse(xml_file_path, parser=parser)
        root = tree.getroot()

        # Standard way to get namespace map. The key is the prefix, value is the URI.
        # If there's a default namespace, the key is None.
        ns = root.nsmap

        # If there is a default namespace, lxml requires a prefix for it in xpath.
        # We can make one up, e.g., 'def'.
        ns_prefix = ''
        if None in ns:
            ns['def'] = ns.pop(None)
            ns_prefix = 'def:'

        def _get_first_text(paths: List[str]) -> Optional[str]:
            for path in paths:
                elements = root.xpath(path, namespaces=ns)
                for element in elements:
                    if element is not None and element.text:
                        value = element.text.strip()
                        if value:
                            return value
            return None

        def _to_float(value: Optional[str]) -> Optional[float]:
            try:
                if value is None or value == "":
                    return None
                return float(value)
            except (TypeError, ValueError):
                return None

        def _to_int(value: Optional[str]) -> Optional[int]:
            try:
                if value is None or value == "":
                    return None
                return int(float(value))
            except (TypeError, ValueError):
                return None

        corners = ['bottomLeft', 'bottomRight', 'topRight', 'topLeft']
        polygon = []

        # --- Parse Orbit Direction ---
        orbit_direction = _get_first_text([
            f".//{ns_prefix}pass",
            f".//{ns_prefix}orbitDirection",
            "//*[local-name()='pass']",
            "//*[local-name()='orbitDirection']",
        ])
        if orbit_direction:
            orbit_direction = orbit_direction.strip().upper()

        # --- Parse Imaging Mode ---
        imaging_mode = _get_first_text([
            f".//{ns_prefix}acquisitionInfo/{ns_prefix}imagingMode",
            f".//{ns_prefix}orderInfo/{ns_prefix}imagingMode",
            "//*[local-name()='acquisitionInfo']/*[local-name()='imagingMode']",
            "//*[local-name()='orderInfo']/*[local-name()='imagingMode']",
        ])

        # --- Parse Polarization ---
        polarization = _get_first_text([
            f".//{ns_prefix}acquisitionInfo/{ns_prefix}polarisationMode",
            f".//{ns_prefix}polarisationList/{ns_prefix}polLayer",
            f".//{ns_prefix}polList/{ns_prefix}polLayer",
            "//*[local-name()='acquisitionInfo']/*[local-name()='polarisationMode']",
            "//*[local-name()='polarisationList']/*[local-name()='polLayer']",
            "//*[local-name()='polList']/*[local-name()='polLayer']",
        ])

        # --- Parse Receiving Station ---
        receiving_station = _get_first_text([
            f".//{ns_prefix}generationInfo/{ns_prefix}receivingStation",
            "//*[local-name()='generationInfo']/*[local-name()='receivingStation']",
        ])

        # --- Parse Satellite Mode ---
        satellite_mode = _get_first_text([
            f".//{ns_prefix}generalHeader/{ns_prefix}satelliteMode",
            f".//{ns_prefix}satelliteMode",
            "//*[local-name()='generalHeader']/*[local-name()='satelliteMode']",
            "//*[local-name()='satelliteMode']",
        ])

        # --- Parse Orbit Circle (Abs Orbit) ---
        orbit_circle = _get_first_text([
            f".//{ns_prefix}missionInfo/{ns_prefix}absOrbit",
            f".//{ns_prefix}absOrbit",
            "//*[local-name()='missionInfo']/*[local-name()='absOrbit']",
            "//*[local-name()='absOrbit']",
        ])

        # --- Scene Center Coordinates ---
        scene_center_lon = _to_float(_get_first_text([
            f".//{ns_prefix}sceneCenterCoord/{ns_prefix}lon",
            "//*[local-name()='sceneCenterCoord']/*[local-name()='lon']",
        ]))
        scene_center_lat = _to_float(_get_first_text([
            f".//{ns_prefix}sceneCenterCoord/{ns_prefix}lat",
            "//*[local-name()='sceneCenterCoord']/*[local-name()='lat']",
        ]))

        # --- Acquisition Time ---
        acquisition_time_utc = _get_first_text([
            f".//{ns_prefix}sceneCenterCoord/{ns_prefix}azimuthTimeUTC",
            f".//{ns_prefix}sceneInfo/{ns_prefix}start/{ns_prefix}timeUTC",
            "//*[local-name()='sceneCenterCoord']/*[local-name()='azimuthTimeUTC']",
            "//*[local-name()='sceneInfo']/*[local-name()='start']/*[local-name()='timeUTC']",
        ])

        # --- Product Type / Level / Unique ID ---
        image_data_type = _get_first_text([
            f".//{ns_prefix}imageDataInfo/{ns_prefix}imageDataType",
            "//*[local-name()='imageDataInfo']/*[local-name()='imageDataType']",
        ])
        product_variant = _get_first_text([
            f".//{ns_prefix}orderInfo/{ns_prefix}productVariant",
            "//*[local-name()='orderInfo']/*[local-name()='productVariant']",
        ])
        image_data_format = _get_first_text([
            f".//{ns_prefix}imageDataInfo/{ns_prefix}imageDataFormat",
            "//*[local-name()='imageDataInfo']/*[local-name()='imageDataFormat']",
        ])
        product_type = image_data_type or product_variant or image_data_format
        product_level = _get_first_text([
            f".//{ns_prefix}generalHeader/{ns_prefix}itemName",
            "//*[local-name()='generalHeader']/*[local-name()='itemName']",
        ])
        product_unique_id = _get_first_text([
            f".//{ns_prefix}logicalProductID",
            f".//{ns_prefix}sceneID",
            "//*[local-name()='logicalProductID']",
            "//*[local-name()='sceneID']",
        ])

        # --- Direction / Start-Layout related fields ---
        look_direction = _get_first_text([
            f".//{ns_prefix}acquisitionInfo/{ns_prefix}lookDirection",
            f".//{ns_prefix}orderInfo/{ns_prefix}lookDirection",
            "//*[local-name()='acquisitionInfo']/*[local-name()='lookDirection']",
            "//*[local-name()='orderInfo']/*[local-name()='lookDirection']",
        ])
        if look_direction:
            look_direction = look_direction.strip().upper()

        image_data_start_with = _get_first_text([
            f".//{ns_prefix}imageDataStartWith",
            "//*[local-name()='imageDataStartWith']",
        ])
        quicklook_data_start_with = _get_first_text([
            f".//{ns_prefix}quicklookDataStartWith",
            "//*[local-name()='quicklookDataStartWith']",
        ])
        geocoded_flag_text = _get_first_text([
            f".//{ns_prefix}geocodedFlag",
            "//*[local-name()='geocodedFlag']",
        ])
        geocoded_flag = None
        if geocoded_flag_text is not None:
            geocoded_flag = geocoded_flag_text.strip().lower() in ("1", "true", "yes", "y")

        # --- Parse Corner Coordinates ---
        corner_details: Dict[str, Dict[str, Any]] = {}
        for corner_name in corners:
            lon_path = f".//{ns_prefix}sceneCornerCoord[@name='{corner_name}']/{ns_prefix}lon"
            lat_path = f".//{ns_prefix}sceneCornerCoord[@name='{corner_name}']/{ns_prefix}lat"
            row_path = f".//{ns_prefix}sceneCornerCoord[@name='{corner_name}']/{ns_prefix}refRow"
            col_path = f".//{ns_prefix}sceneCornerCoord[@name='{corner_name}']/{ns_prefix}refColumn"
 
            lon_elements = root.xpath(lon_path, namespaces=ns)
            lat_elements = root.xpath(lat_path, namespaces=ns)
            row_elements = root.xpath(row_path, namespaces=ns)
            col_elements = root.xpath(col_path, namespaces=ns)
 
            if lon_elements and lat_elements and lon_elements[0].text and lat_elements[0].text:
                lon = float(lon_elements[0].text)
                lat = float(lat_elements[0].text)
                polygon.append((lon, lat))
                ref_row = _to_int(row_elements[0].text if row_elements and row_elements[0].text else None)
                ref_col = _to_int(col_elements[0].text if col_elements and col_elements[0].text else None)
                corner_details[corner_name] = {
                    "lon": lon,
                    "lat": lat,
                    "ref_row": ref_row,
                    "ref_col": ref_col,
                }
            else:
                # This might not be a critical error if we just want the orbit direction
                # but for now, we'll keep it strict.
                print(f"Warning: Could not find or parse corner '{corner_name}' in XML '{xml_name}'")
                return None, None
        
        if len(polygon) == 4:
            ordered_polygon = _ordered_closed_polygon_from_corner_details(corner_details)
            if not ordered_polygon:
                return None, None
            polygon = ordered_polygon
            corner_pixel_mapping = build_corner_pixel_mapping(corner_details)
            meta = {
                "orbit_direction": orbit_direction,
                "imaging_mode": imaging_mode,
                "polarization": polarization,
                "receiving_station": receiving_station,
                "satellite_mode": satellite_mode,
                "orbit_circle": orbit_circle,
                "scene_center_lon": scene_center_lon,
                "scene_center_lat": scene_center_lat,
                "acquisition_time_utc": acquisition_time_utc,
                "product_type": product_type,
                "image_data_type": image_data_type,
                "image_data_format": image_data_format,
                "product_variant": product_variant,
                "product_level": product_level,
                "product_unique_id": product_unique_id,
                "look_direction": look_direction,
                "image_data_start_with": image_data_start_with,
                "quicklook_data_start_with": quicklook_data_start_with,
                "geocoded_flag": geocoded_flag,
                "corner_ref_pixels": {
                    key: {
                        "ref_row": value.get("ref_row"),
                        "ref_col": value.get("ref_col"),
                    }
                    for key, value in corner_details.items()
                },
                "corner_pixel_mapping": corner_pixel_mapping,
            }
            return polygon, meta
        
        return None, None

    except Exception as exc:
        print(f"Error parsing XML metadata in '{xml_name}': {exc.__class__.__name__}")
        return None, None
