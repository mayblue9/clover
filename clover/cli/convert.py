import glob
from datetime import datetime
import re
from netCDF4 import Dataset

import numpy
import click
from pyproj import Proj
import rasterio
from rasterio.crs import CRS
from rasterio import get_data_window, window_union

from clover.cli import cli
from clover.netcdf.variable import SpatialCoordinateVariables, DateVariable
from clover.netcdf.crs import set_crs
from clover.netcdf.utilities import get_pack_atts, get_fill_value
from clover.geometry.bbox import BBox


DATE_REGEX = re.compile('%[yY]')  # TODO: add all appropriate strftime directives


@cli.command(short_help='Convert rasters to NetCDF')
@click.argument('files')
@click.argument('output', type=click.Path())
@click.argument('variable', type=click.STRING)
@click.option('--dtype', type=click.Choice(['float32', 'float64', 'int8', 'int16', 'int32', 'uint8', 'uint16', 'uint32']), default=None, help='Data type of output variable.  Will be inferred from input raster if not provided.')
@click.option('--src-crs', default=None, type=click.STRING, help='Source coordinate reference system (limited to EPSG codes, e.g., EPSG:4326).  Will be read from file if not provided.')
@click.option('--x', 'x_name', type=click.STRING, help='Name of x dimension and variable (default: lon or x)')
@click.option('--y', 'y_name', type=click.STRING, help='Name of y dimension and variable (default: lat or y)')
@click.option('--z', 'z_name', type=click.STRING, default='time', help='Name of z dimension and variable', show_default=True)
@click.option('--netcdf3', is_flag=True, default=False, help='Output in NetCDF3 version instead of NetCDF4')
@click.option('--zip', is_flag=True, default=False, help='Use zlib compression of data and coordinate variables')
@click.option('--packed', is_flag=True, default=False, help='Pack floating point values into an integer (will lose precision)')
@click.option('--xy-dtype', type=click.Choice(['float32', 'float64']), default='float32', help='Data type of spatial coordinate variables.', show_default=True)
# @click.option('--z-dtype', type=click.Choice(['float32', 'float64', 'int8', 'int16', 'int32', 'uint8', 'uint16', 'uint32']), default=None, help='Data type of z variable.  Will be inferred from values if not provided.')
@click.option('--calendar', type=click.STRING, default='standard', help='Calendar to use if z dimension is a date type', show_default=True)
@click.option('--autocrop', is_flag=True, default=False, help='Automatically crop to data bounds (trim NODATA)')
def to_netcdf(
    files,
    output,
    variable,
    dtype,
    src_crs,
    x_name,
    y_name,
    z_name,
    netcdf3,
    zip,
    packed,
    xy_dtype,
    # z_dtype,
    calendar,
    autocrop):
    """
    Convert rasters to NetCDF and stack them according to a dimension.

    X and Y dimension names will be named according to the source projection (lon, lat if geographic projection, x, y
    otherwise) unless specified.

    Will overwrite an existing NetCDF file.

    Only the first band of the input will be turned into a NetCDF file.
    """


    # TODO: add format string template to this to parse out components
    # Need to be able to sort things in the right order and stack them into the appropriate dimension
    file_regex = None
    date_format = ''
    if '%' in files:
        # Parse out dates according to datetime.strftime rules, replace
        directives = re.findall(DATE_REGEX, files)

        if not directives:
            raise click.BadParameter('Invalid pattern', param='FILES', param_hint='FILES')

        date_format = ''.join(directives)

        for d in directives:
            pattern = '[0-9][0-9]'
            if d == '%Y':
                pattern += pattern
            file_regex = re.compile(pattern)
            files = files.replace(d, pattern)

    filenames = glob.glob(files)
    if not filenames:
        raise click.BadParameter('No files found matching that pattern', param='files', param_hint='FILES')

    z_values = []
    if file_regex:
        pairs = []
        for filename in filenames:
            date_obj = datetime.strptime(file_regex.search(filename).group(), date_format)
            pairs.append((date_obj, filename))

        # pairs = [(int(file_regex.search(f).group()), f) for f in filenames]
        pairs = sorted(pairs, key=lambda x: x[0])
        z_values = [item[0] for item in pairs]
        filenames = [item[1] for item in pairs]

    items = tuple(enumerate(filenames))

    has_z = len(filenames) > 1

    if has_z and not z_name:
        raise click.BadParameter('Required when > 1 input file', param='--z', param_hint='--z')

    if src_crs:
        src_crs = CRS.from_string(src_crs)

    template_ds = rasterio.open(filenames[0])
    src_crs = template_ds.crs or src_crs

    if not src_crs:
        raise click.BadParameter('Required when no CRS information available in source files', param='--src-crs',
                                 param_hint='--src-crs')

    prj = Proj(**src_crs.to_dict())
    bounds = template_ds.bounds
    width = template_ds.width
    height = template_ds.height
    window = None

    src_dtype = numpy.dtype(template_ds.dtypes[0])
    dtype = numpy.dtype(dtype) if dtype else src_dtype

    if dtype == src_dtype:
        fill_value = template_ds.nodata
        if src_dtype.kind in ('u', 'i'):
            # nodata always comes from rasterio as floating point
            fill_value = int(fill_value)
    else:
        fill_value = get_fill_value(dtype)

    x_name = x_name or ('lon' if src_crs.is_geographic else 'x')
    y_name = y_name or ('lat' if src_crs.is_geographic else 'y')

    var_kwargs = {
        'fill_value': fill_value
    }

    format = 'NETCDF3_CLASSIC' if netcdf3 else 'NETCDF4'

    with Dataset(output, 'w', format=format) as out:
        if packed or autocrop:
            mins = []
            maxs = []
            windows = []

            click.echo('Inspecting input datasets...')
            with click.progressbar(items) as iter:
                for index, filename in iter:
                    with rasterio.open(filename) as src:
                        data = src.read(1, masked=True)
                        if packed:
                            mins.append(data.min())
                            maxs.append(data.max())
                        if autocrop:
                            data_window = get_data_window(data)
                            if data_window != ((0, height), (0, width)):
                                windows.append(data_window)

            if packed:
                min_value = min(mins)
                max_value = max(maxs)
                scale, offset = get_pack_atts(dtype, min_value, max_value)
            if autocrop and windows:
                window = window_union(windows)
                bounds = template_ds.window_bounds(window)
                height = window[0][1] - window[0][0]
                width = window[1][1] - window[1][0]

        coords = SpatialCoordinateVariables.from_bbox(BBox(bounds, prj), width, height, xy_dtype)
        coords.add_to_dataset(out, x_name, y_name, zlib=zip)

        var_dimensions = [y_name, x_name]
        shape = list(coords.shape)
        if has_z:
            shape.insert(0, len(filenames))
            out.createDimension(z_name, shape[0])
            var_dimensions.insert(0, z_name)
            if z_values:
                dates = DateVariable(numpy.array(z_values),
                                     units_start_date=z_values[0], calendar=calendar)
                dates.add_to_dataset(out, z_name)


        click.echo('Creating {0}:{1} with shape {2}'.format(output, variable, shape))

        out_var = out.createVariable(variable, dtype, dimensions=var_dimensions,
                                     zlib=zip, **var_kwargs)
        set_crs(out, variable, prj, set_proj4_att=True)

        if packed:
            out_var.setncattr('scale_factor', scale)
            out_var.setncattr('add_offset', offset)



        click.echo('Copying data from input files...')
        with click.progressbar(items) as iter:
            for index, filename in iter:
                with rasterio.open(filename) as src:
                    data = src.read(1, masked=True, window=window)

                    if has_z:
                        out_var[index, :] = data
                    else:
                        out_var[:] = data

                out.sync()
