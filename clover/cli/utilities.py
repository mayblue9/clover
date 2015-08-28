import importlib
import numpy
from PIL.Image import ANTIALIAS
from pyproj import Proj
from clover.utilities.color import Color
from clover.netcdf.utilities import collect_statistics
from clover.render.renderers.stretched import StretchedRenderer


def render_image(renderer, data, filename, scale=1, flip_y=False):
    if flip_y:
        data = data[::-1]

    img = renderer.render_image(data)
    if scale != 1:
        img = img.resize((numpy.array(data.shape[::-1]) * scale).astype(numpy.uint), ANTIALIAS)
    img.save(filename)


def colormap_to_stretched_renderer(colormap, colorspace='hsv', filenames=None, variable=None):
    statistics = None
    if 'min:' in colormap or 'max:' in colormap or 'mean' in colormap:
        if not filenames and variable:
            raise ValueError('filenames and variable are required inputs to use colormap with statistics')
        statistics = collect_statistics(filenames, (variable,))[variable]

    colors = []
    for entry in colormap.split(','):
        value, color = entry.split(':')
        # TODO: add proportions of statistics
        if value in ('min', 'max', 'mean'):
            value = statistics[value]
        else:
            value = float(value)
        colors.append((value, Color.from_hex(color)))

    return StretchedRenderer(colors, colorspace=colorspace)


def palette_to_stretched_renderer(palette_path, values, filenames=None, variable=None):
    index = palette_path.rindex('.')
    palette = getattr(importlib.import_module('palettable.' + palette_path[:index]), palette_path[index+1:])

    values = values.split(',')
    if not len(values) > 1:
        raise ValueError('Must provide at least 2 values for palette-based stretched renderer')

    statistics = None
    if 'min' in values or 'max' in values:
        if not filenames and variable:
            raise ValueError('filenames and variable are required inputs to use palette with statistics')
        statistics = collect_statistics(filenames, (variable,))[variable]

        for statistic in ('min', 'max'):
            if statistic in values:
                values[values.index(statistic)] = statistics[statistic]

    hex_colors = palette.hex_colors

    # TODO: this only works cleanly for min:max or 2 endpoint values.  Otherwise require that the number of palette colors match the number of values

    colors = [(values[0], Color.from_hex(hex_colors[0]))]

    intermediate_colors = hex_colors[1:-1]
    if intermediate_colors:
        interval = (values[-1] - values[0]) / (len(intermediate_colors) + 1)
        for i, color in enumerate(intermediate_colors):
            colors.append((values[0] + (i + 1) * interval, Color.from_hex(color)))

    colors.append((values[-1], Color.from_hex(hex_colors[-1])))

    return StretchedRenderer(colors, colorspace='rgb')  # I think all palettable palettes are in RGB ramps


def get_leaflet_anchors(bbox):
    """
    Returns Leaflet anchor coordinates for creating an ImageOverlay layer.
    """

    wgs84_bbox = bbox.project(Proj(init='EPSG:4326'))
    return [[wgs84_bbox.ymin, wgs84_bbox.xmin], [wgs84_bbox.ymax, wgs84_bbox.xmax]]