from matplotlib import pyplot as plt
import rasterio
import random


def tiff_overview(tiff_url):
    """
    Plot the a little version of the map (thumbnail)
    """
    with rasterio.open(tiff_url) as dataset:
        oviews = dataset.overviews(1)  # list of overviews from biggest to smallest
        oview = oviews[-1]  # let's look at the smallest thumbnail
        print('Decimation factor= {}'.format(oview))
        # NOTE this is using a 'decimated read' (http://rasterio.readthedocs.io/en/latest/topics/resampling.html)
        thumbnail = dataset.read(1, out_shape=(1, int(dataset.height // oview), int(dataset.width // oview)))

    print('array type: ', type(thumbnail))

    plt.figure(figsize=(5, 5))
    plt.imshow(thumbnail)
    plt.colorbar()
    plt.title('Overview - Band 4 {}'.format(thumbnail.shape))
    plt.xlabel('Column #')
    plt.ylabel('Row #')


def plot_map(image, title, x_label="", y_label=""):
    plt.figure(figsize=(10, 15))
    plt.imshow(image)
    plt.colorbar(shrink=0.5)
    plt.title(title)
    plt.xlabel(x_label)
    plt.ylabel(y_label)
