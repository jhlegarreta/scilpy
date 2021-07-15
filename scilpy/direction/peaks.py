# -*- coding: utf-8 -*-
import numpy as np


def peak_directions_asym(odf, sphere, relative_peak_threshold=.5,
                         min_separation_angle=25):
    """Get the directions of odf peaks.

    Peaks are defined as points on the odf that are greater than at least one
    neighbor and greater than or equal to all neighbors. Peaks are sorted in
    descending order by their values then filtered based on their relative size
    and spacing on the sphere. An odf may have 0 peaks, for example if the odf
    is perfectly isotropic.

    Parameters
    ----------
    odf : 1d ndarray
        The odf function evaluated on the vertices of `sphere`
    sphere : Sphere
        The Sphere providing discrete directions for evaluation.
    relative_peak_threshold : float in [0., 1.]
        Only peaks greater than ``min + relative_peak_threshold * scale`` are
        kept, where ``min = max(0, odf.min())`` and
        ``scale = odf.max() - min``.
    min_separation_angle : float in [0, 90]
        The minimum distance between directions. If two peaks are too close
        only the larger of the two is returned.

    Returns
    -------
    directions : (N, 3) ndarray
        N vertices for sphere, one for each peak
    values : (N,) ndarray
        peak values
    indices : (N,) ndarray
        peak indices of the directions on the sphere

    Notes
    -----
    If the odf has any negative values, they will be clipped to zeros.

    """
    values, indices = local_maxima(odf, sphere.edges)

    # If there is only one peak return
    n = len(values)
    if n == 0 or (values[0] < 0.):
        return np.zeros((0, 3)), np.zeros(0), np.zeros(0, dtype=int)
    elif n == 1:
        return sphere.vertices[indices], values, indices

    odf_min = np.min(odf)
    odf_min = odf_min if (odf_min >= 0.) else 0.
    # because of the relative threshold this algorithm will give the same peaks
    # as if we divide (values - odf_min) with (odf_max - odf_min) or not so
    # here we skip the division to increase speed
    values_norm = (values - odf_min)

    # Remove small peaks
    n = search_descending(values_norm, relative_peak_threshold)
    indices = indices[:n]
    directions = sphere.vertices[indices]

    # Remove peaks too close together
    directions, uniq = remove_similar_vertices(directions,
                                               min_separation_angle,
                                               return_index=True,
                                               asym=True)
    values = values[uniq]
    indices = indices[uniq]
    return directions, values, indices


def remove_similar_vertices(vertices, theta,
                            return_index=False,
                            asym=False):
    """Remove vertices that are less than `theta` degrees from any other

    Returns vertices that are at least theta degrees from any other vertex.
    Vertex v and -v are considered the same so if v and -v are both in
    `vertices` only one is kept. Also if v and w are both in vertices, w must
    be separated by theta degrees from both v and -v to be unique.

    Parameters
    ----------
    vertices : (N, 3) ndarray
        N unit vectors.
    theta : float
        The minimum separation between vertices in degrees.
    return_mapping : {False, True}, optional
        If True, return `mapping` as well as `vertices` and maybe `indices`
        (see below).
    return_indices : {False, True}, optional
        If True, return `indices` as well as `vertices` and maybe `mapping`
        (see below).

    Returns
    -------
    unique_vertices : (M, 3) ndarray
        Vertices sufficiently separated from one another.
    mapping : (N,) ndarray
        For each element ``vertices[i]`` ($i \in 0..N-1$), the index $j$ to a
        vertex in `unique_vertices` that is less than `theta` degrees from
        ``vertices[i]``.  Only returned if `return_mapping` is True.
    indices : (N,) ndarray
        `indices` gives the reverse of `mapping`.  For each element
        ``unique_vertices[j]`` ($j \in 0..M-1$), the index $i$ to a vertex in
        `vertices` that is less than `theta` degrees from
        ``unique_vertices[j]``.  If there is more than one element of
        `vertices` that is less than theta degrees from `unique_vertices[j]`,
        return the first (lowest index) matching value.  Only return if
        `return_indices` is True.
    """
    if vertices.shape[1] != 3:
        raise ValueError('Vertices should be 2D with second dim length 3')

    n_unique = 0
    # Large enough for all possible sizes of vertices
    n = vertices.shape[0]
    cos_similarity = np.cos(np.pi/180 * theta)
    unique_vertices = np.empty((n, 3), dtype=float)
    if return_index:
        index = np.empty(n, dtype=np.uint16)

    for i in range(n):
        pass_all = True
        # Check all other accepted vertices for similarity to this one
        sim = vertices[i].dot(unique_vertices.T)
        if not asym:
            sim = np.abs(sim)
        if (sim > cos_similarity).any():  # too similar, drop
            pass_all = False
            # This point unique_vertices[j] already has an entry in index,
            # so we do not need to update.
            break
        if pass_all:  # none similar, keep
            unique_vertices[n_unique] = vertices[i]
            if return_index:
                index[n_unique] = i
            n_unique += 1

    verts = unique_vertices[:n_unique].copy()
    if not return_index:
        return verts
    out = [verts]
    if return_index:
        out.append(index[:n_unique].copy())
    return out


def search_descending(a, relative_threshold):
    """`i` in descending array `a` so `a[i] < a[0] * relative_threshold`

    Call ``T = a[0] * relative_threshold``. Return value `i` will be the
    smallest index in the descending array `a` such that ``a[i] < T``.
    Equivalently, `i` will be the largest index such that ``all(a[:i] >= T)``.
    If all values in `a` are >= T, return the length of array `a`.

    Parameters
    ----------
    a : ndarray, ndim=1, c-contiguous
        Array to be searched.  We assume `a` is in descending order.
    relative_threshold : float
        Applied threshold will be ``T`` with ``T = a[0] * relative_threshold``.

    Returns
    -------
    i : np.intp
        If ``T = a[0] * relative_threshold`` then `i` will be the largest index
        such that ``all(a[:i] >= T)``.  If all values in `a` are >= T then
        `i` will be `len(a)`.

    Examples
    --------
    >>> a = np.arange(10, 0, -1, dtype=float)
    >>> a
    array([ 10.,   9.,   8.,   7.,   6.,   5.,   4.,   3.,   2.,   1.])
    >>> search_descending(a, 0.5)
    6
    >>> a < 10 * 0.5
    array([False, False, False, False, False, False,
           True,  True,  True,  True], dtype=bool)
    >>> search_descending(a, 1)
    1
    >>> search_descending(a, 2)
    0
    >>> search_descending(a, 0)
    10
    """
    if a.shape[0] == 0:
        return 0

    threshold = relative_threshold * a[0]
    indice = np.where(a > threshold)[0][-1] + 1
    return indice


def local_maxima(odf, edges):
    """Local maxima of a function evaluated on a discrete set of points.

    If a function is evaluated on some set of points where each pair of
    neighboring points is an edge in edges, find the local maxima.

    Parameters
    ----------
    odf : array, 1d, dtype=double
        The function evaluated on a set of discrete points.
    edges : array (N, 2)
        The set of neighbor relations between the points. Every edge, ie
        `edges[i, :]`, is a pair of neighboring points.

    Returns
    -------
    peak_values : ndarray
        Value of odf at a maximum point. Peak values is sorted in descending
        order.
    peak_indices : ndarray
        Indices of maximum points. Sorted in the same order as `peak_values` so
        `odf[peak_indices[i]] == peak_values[i]`.

    Notes
    -----
    A point is a local maximum if it is > at least one neighbor and >= all
    neighbors. If no points meet the above criteria, 1 maximum is returned such
    that `odf[maximum] == max(odf)`.

    See Also
    --------
    dipy.core.sphere

    """

    wpeak, count = _compare_neighbors(odf, edges)
    if count == -1:
        raise IndexError("Values in edges must be < len(odf)")
    elif count == -2:
        raise ValueError("odf can not have nans")
    indices = wpeak[:count].copy()
    # Get peak values return
    values = np.take(odf, indices)
    # Sort both values and indices
    values, indices = _cosort(values, indices)
    return values, indices


def _cosort(A, B):
    """Sorts `A` in-place and applies the same reordering to `B`"""
    n = A.shape[0]

    for i in range(1, n):
        insert_A = A[i]
        insert_B = B[i]
        hole = i
        while hole > 0 and insert_A > A[hole - 1]:
            A[hole] = A[hole - 1]
            B[hole] = B[hole - 1]
            hole -= 1
        A[hole] = insert_A
        B[hole] = insert_B

    return A, B


def _compare_neighbors(odf, edges):
    """Compares every pair of points in edges

    Parameters
    ----------
    odf : array of double
        values of points on sphere.
    edges : array of uint16
        neighbor relationships on sphere. Every set of neighbors on the sphere
        should be an edge.
    wpeak_ptr : pointer
        pointer to a block of memory which will be updated with the result of
        the comparisons. This block of memory must be large enough to hold
        len(odf) longs. The first `count` elements of wpeak will be updated
        with the indices of the peaks.

    Returns
    -------
    count : long
        Number of maxima in odf. A value < 0 indicates an error:
            -1 : value in edges too large, >= than len(odf)
            -2 : odf contains nans
    """
    wpeak = np.zeros((odf.shape[0],), dtype=np.intp)
    lenedges = edges.shape[0]
    lenodf = odf.shape[0]
    count = 0

    for i in range(lenedges):

        find0 = edges[i, 0]
        find1 = edges[i, 1]
        if find0 >= lenodf or find1 >= lenodf:
            count = -1
            break
        odf0 = odf[find0]
        odf1 = odf[find1]

        """
        Here `wpeak` is used as an indicator array that can take one of
        three values.  If `wpeak[i]` is:
        * -1 : point i of the sphere is smaller than at least one neighbor.
        *  0 : point i is equal to all its neighbors.
        *  1 : point i is > at least one neighbor and >= all its neighbors.

        Each iteration of the loop is a comparison between neighboring points
        (the two point of an edge). At each iteration we update wpeak in
        the following way::

            wpeak[smaller_point] = -1
            if wpeak[larger_point] == 0:
                wpeak[larger_point] = 1

        If the two points are equal, wpeak is left unchanged.
        """
        if odf0 < odf1:
            wpeak[find0] = -1
            wpeak[find1] |= 1
        elif odf0 > odf1:
            wpeak[find0] |= 1
            wpeak[find1] = -1
        elif (odf0 != odf0) or (odf1 != odf1):
            count = -2
            break

    if count < 0:
        return count

    # Count the number of peaks and use first count elements of wpeak_ptr to
    # hold indices of those peaks
    for i in range(lenodf):
        if wpeak[i] > 0:
            wpeak[count] = i
            count += 1

    return wpeak, count
