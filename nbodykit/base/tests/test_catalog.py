from runtests.mpi import MPITest
from nbodykit.lab import *
from nbodykit import setup_logging, set_options

import pytest
from numpy.testing import assert_allclose, assert_array_equal

setup_logging()


@MPITest([1, 4])
def test_consecutive_selections(comm):

    CurrentMPIComm.set(comm)

    # compute with smaller chunk size to test chunking
    with set_options(dask_chunk_size=100):

        s = UniformCatalog(1000, 1.0, seed=42)

        # slice once
        subset1 = s[:20]
        assert 'selection' in subset1['Position'].name # ensure optimized selection succeeded

        # slice again
        subset2 = subset1[:10]
        assert 'selection' in subset2['Position'].name # ensure optimized selection succeeded

        assert_array_equal(subset2['Position'].compute(), s['Position'].compute()[:20][:10])

@MPITest([1, 4])
def test_optimized_selection(comm):

    CurrentMPIComm.set(comm)

    # compute with smaller chunk size to test chunking
    with set_options(dask_chunk_size=100):
        s = RandomCatalog(1000, seed=42)

        # ra, dec, z
        s['z']   = s.rng.normal(loc=0, scale=0.2, size=s.size) # contains z < 0
        s['ra']  = s.rng.uniform(low=110, high=260, size=s.size)
        s['dec'] = s.rng.uniform(low=-3.6, high=60., size=s.size)

        # raises exception due to z<0
        with pytest.raises(Exception):
            s['Position'] = transform.SkyToCartesion(s['ra'], s['dec'], s['z'], cosmo=cosmology.Planck15)
            pos = s['Position'].compute()

        # slice (even after adding Position column)
        subset = s[s['z'] > 0]

        # Position should be evaluatable due to slicing first, then evaluating operations
        pos = subset['Position'].compute()

@MPITest([1, 4])
def test_file_optimized_selection(comm):

    import tempfile
    CurrentMPIComm.set(comm)

    # compute with smaller chunk size to test chunking
    with set_options(dask_chunk_size=100):


        with tempfile.NamedTemporaryFile() as ff:

            # generate data
            data = numpy.random.random(size=(1000,5))
            numpy.savetxt(ff, data, fmt='%.7e'); ff.seek(0)

            # read nrows
            names =['a', 'b', 'c', 'd', 'e']
            s = CSVCatalog(ff.name, names, blocksize=100)

            # add a complicated weight
            s['WEIGHT'] = s['a'] * (s['b'] + s['c'] - 1.0)

            # test selecting a range
            valid = (s['a'] > 0.3)&(s['a'] < 0.7)
            index = valid.compute()

            # slice (even after adding Position column)
            subset = s[valid]

            # verify all columns
            for col in s:
                assert_array_equal(subset[col].compute(), s[col].compute()[index])

@MPITest([1, 4])
def test_save(comm):

    cosmo = cosmology.Planck15
    CurrentMPIComm.set(comm)

    import tempfile
    import shutil

    # initialize an output directory
    if comm.rank == 0:
        tmpfile = tempfile.mkdtemp()
    else:
        tmpfile = None
    tmpfile = comm.bcast(tmpfile)

    # initialize a uniform catalog
    source = UniformCatalog(nbar=0.2e-2, BoxSize=1024., seed=42)

    # add a non-array attrs (saved as JSON)
    source.attrs['empty'] = None

    # save to a BigFile
    source.save(tmpfile, ['Position', 'Velocity'])

    # load as a BigFileCatalog
    source2 = BigFileCatalog(tmpfile, header='Header', attrs={"Nmesh":32})

    # check sources
    for k in source.attrs:
        assert_array_equal(source2.attrs[k], source.attrs[k])

    # check the data
    def allconcat(data):
        return numpy.concatenate(comm.allgather(data), axis=0)
    assert_allclose(allconcat(source['Position']), allconcat(source2['Position']))
    assert_allclose(allconcat(source['Velocity']), allconcat(source2['Velocity']))

    comm.barrier()
    if comm.rank == 0:
        shutil.rmtree(tmpfile)

@MPITest([1, 4])
def test_tomesh(comm):
    CurrentMPIComm.set(comm)

    source = UniformCatalog(nbar=0.2e-2, BoxSize=1024., seed=42)
    source['Weight0'] = source['Velocity'][:, 0]
    source['Weight1'] = source['Velocity'][:, 1]
    source['Weight2'] = source['Velocity'][:, 2]

    mesh = source.to_mesh(Nmesh=128, compensated=True)
    assert_allclose(source['Position'], mesh['Position'])

    mesh = source.to_mesh(Nmesh=128, compensated=True, interlaced=True)
    assert_allclose(source['Position'], mesh['Position'])

    mesh = source.to_mesh(Nmesh=128, weight='Weight0')

    # bad weight name
    with pytest.raises(ValueError):
        mesh = source.to_mesh(Nmesh=128, weight='Weight3')

    # bad window name
    with pytest.raises(ValueError):
        mesh = source.to_mesh(Nmesh=128, weight='Weight0', window='bad_window')

    # missing Nmesh
    with pytest.raises(ValueError):
        mesh = source.to_mesh()

    # missing BoxSize
    BoxSize = source.attrs.pop("BoxSize")
    with pytest.raises(ValueError):
        mesh = source.to_mesh(Nmesh=128)

@MPITest([4])
def test_bad_column(comm):
    CurrentMPIComm.set(comm)
    source = UniformCatalog(nbar=0.2e-3, BoxSize=1024., seed=42)

    # read a missing column
    with pytest.raises(ValueError):
        data = source.read(['BAD_COLUMN'])

    # read a missing column
    with pytest.raises(AttributeError):
        data = source.get_hardcolumn('BAD_COLUMN')

@MPITest([4])
def test_slice(comm):
    CurrentMPIComm.set(comm)

    source = UniformCatalog(nbar=0.2e-3, BoxSize=1024., seed=42)

    # slice a subset
    subset = source[:10]
    assert all(col in subset for col in source.columns)
    assert isinstance(subset, source.__class__)
    assert len(subset) == 10
    assert_array_equal(subset['Position'], source['Position'].compute()[:10])

    subset = source[[0,1,2]]
    assert_array_equal(subset['Position'], source['Position'].compute()[[0,1,2]])

    # cannot slice with list of floats
    with pytest.raises(KeyError):
        subset = source[[0.0,1.0,2.0]]

    # missing column
    with pytest.raises(KeyError):
        col = source['BAD_COLUMN']

@MPITest([4])
def test_dask_slice(comm):
    CurrentMPIComm.set(comm)

    source = UniformCatalog(nbar=0.2e-3, BoxSize=1024., seed=42)

    # add a selection column
    index = numpy.random.choice([True, False], size=len(source))
    source['Selection'] = index

    # slice a column with a dask array
    pos = source['Position']
    pos2 = pos[source['Selection']]
    assert_array_equal(pos.compute()[index], pos2.compute())

@MPITest([1 ,4])
def test_transform(comm):
    cosmo = cosmology.Planck15
    CurrentMPIComm.set(comm)
    data = numpy.ones(100, dtype=[
            ('Position', ('f4', 3)),
            ('Velocity', ('f4', 3))]
            )

    source = ArrayCatalog(data, BoxSize=100, Nmesh=32)

    source['Velocity'] = source['Position'] + source['Velocity']

    source['Position'] = source['Position'] + source['Velocity']

    # Position triggers  Velocity which triggers Position and Velocity
    # which resolves to the true data.
    # so total is 3.
    assert_allclose(source['Position'], 3)

    mesh = source.to_mesh()

@MPITest([1, 4])
def test_getitem_columns(comm):
    CurrentMPIComm.set(comm)

    source = UniformCatalog(nbar=0.2e-3, BoxSize=1024., seed=42)

    # bad column name
    with pytest.raises(KeyError):
        subset = source[['Position', 'BAD_COLUMN']]

    subset = source[['Position']]

    for col in subset:
        assert_array_equal(subset[col].compute(), source[col].compute())

@MPITest([1, 4])
def test_delitem(comm):

    CurrentMPIComm.set(comm)
    source = UniformCatalog(nbar=0.2e-3, BoxSize=1024., seed=42)

    # add a test column
    test = numpy.ones(source.size)
    source['test'] = test

    # cannot delete hard coded column
    with pytest.raises(ValueError):
        del source['Position']

    # cannot delete missing column
    with pytest.raises(ValueError):
        del source['BAD_COLUMN']

    assert 'test' in source
    del source['test']
    assert 'test' not in source

def test_columnaccessor():
    from nbodykit.base.catalog import ColumnAccessor
    source = UniformCatalog(nbar=0.2e-3, BoxSize=1024., seed=42)

    c = source['Position']
    truth = c[0].compute()

    assert isinstance(c, ColumnAccessor)
    c *= 10.
    # c is no longer an accessor because it has transformed.
    assert not isinstance(c, ColumnAccessor)

    # thus it is not affecting original.
    assert_array_equal(source['Position'][0].compute(), truth)
    assert_array_equal(c[0].compute(), truth * 10.)

    # inplace modification is still OK
    source['Position'] *= 10
    assert_array_equal(source['Position'][0].compute(), truth * 10)

    # test pretty print.
    assert 'first' in str(source['Position'])
    assert 'last' in str(source['Position'])

    # test circular reference
    new_col = source['Selection']
    assert isinstance(new_col, ColumnAccessor)
    source['Selection2'] = new_col
    assert source['Selection'].catalog is source
    assert source['Selection2'].catalog is source

@MPITest([1, 4])
def test_copy(comm):

    CurrentMPIComm.set(comm)
    source = UniformCatalog(nbar=0.2e-3, BoxSize=1024., seed=42)
    source['TEST'] = 10
    source.attrs['TEST'] = 'TEST'

    # store original data
    data = {}
    for col in source:
        data[col] = source[col].compute()

    # make copy
    copy = source.copy()

    # modify original
    source['Position'] += 100.
    source['Velocity'] *= 10.

    # check data is equal to original
    for col in copy:
        assert_array_equal(copy[col].compute(), data[col])

    # check meta-data
    for k in source.attrs:
        assert k in copy.attrs

@MPITest([4])
def test_view(comm):
    CurrentMPIComm.set(comm)

    # the CatalogSource
    source = UniformCatalog(nbar=0.2e-3, BoxSize=1024., seed=42)
    source['TEST'] = 10.
    source.attrs['TEST'] = 10.0

    # view
    view = source.view()
    assert view.base is source
    assert isinstance(view, source.__class__)

    # check meta-data
    for k in source.attrs:
        assert k in view.attrs

    # adding columns to the view changes original source
    view['TEST2'] = 5.0
    assert 'TEST2' in source
