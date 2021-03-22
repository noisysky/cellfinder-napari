import numpy as np
from qtpy import QtCore
from pathlib import Path


from qtpy.QtWidgets import (
    QPushButton,
    QLabel,
    QComboBox,
    QWidget,
    QFileDialog,
    QGridLayout,
    QGroupBox,
)

from brainglobe_napari_io.cellfinder.utils import convert_layer_to_cells
from imlib.cells.cells import Cell
from imlib.IO.cells import cells_xml_to_df, save_cells, get_cells
from imlib.general.system import get_sorted_file_paths, ensure_directory_exists
from imlib.general.list import unique_elements_lists

from cellfinder_core.extract.extract_cubes import main as extract_cubes_main
from .utils import add_combobox, add_button, display_info

import napari


# Constants used throughout
WINDOW_HEIGHT = 750
WINDOW_WIDTH = 1500
COLUMN_WIDTH = 150


class CurationWidget(QWidget):
    def __init__(
        self,
        viewer: napari.viewer.Viewer,
        cube_depth=50,
        cube_width=20,
        cube_height=20,
        network_voxel_sizes=[5, 1, 1],
        n_free_cpus=2,
        save_empty_cubes=False,
    ):
        super(CurationWidget, self).__init__()

        self.non_cells_to_extract = None
        self.cells_to_extract = None

        self.cube_depth = cube_depth
        self.cube_width = cube_width
        self.cube_height = cube_height
        self.network_voxel_sizes = network_voxel_sizes
        self.n_free_cpus = n_free_cpus
        self.save_empty_cubes = save_empty_cubes

        self.viewer = viewer

        self.signal_layer = None
        self.background_layer = None
        self.training_data_cell_layer = None
        self.training_data_non_cell_layer = None

        self.image_layer_names = self._get_layer_names()
        self.point_layer_names = self._get_layer_names(
            layer_type=napari.layers.Points
        )

        self.output_directory = None

        self.setup_main_layout()

        @self.viewer.layers.events.connect
        def update_layer_list(v):
            self.image_layer_names = self._get_layer_names()
            self.point_layer_names = self._get_layer_names(
                layer_type=napari.layers.Points
            )
            self.signal_image_choice.clear()
            self._update_combobox_options(
                self.signal_image_choice, self.image_layer_names
            )
            self._update_combobox_options(
                self.background_image_choice, self.image_layer_names
            )
            self._update_combobox_options(
                self.training_data_cell_choice, self.point_layer_names
            )
            self._update_combobox_options(
                self.training_data_non_cell_choice, self.point_layer_names
            )

    @staticmethod
    def _update_combobox_options(combobox, options_list):
        combobox.clear()
        combobox.addItems(options_list)

    def _get_layer_names(self, layer_type=napari.layers.Image):
        return [
            layer.name
            for layer in self.viewer.layers
            if type(layer) == layer_type
        ]

    def setup_main_layout(self):
        """
        Construct main layout of widget
        """
        self.layout = QGridLayout()
        self.layout.setContentsMargins(10, 10, 10, 10)
        self.layout.setAlignment(QtCore.Qt.AlignTop)
        self.layout.setSpacing(4)

        self.add_loading_panel(1)

        self.status_label = QLabel()
        self.status_label.setText("Ready")
        self.layout.addWidget(self.status_label, 7, 0)

        self.setLayout(self.layout)

    def add_loading_panel(self, row, column=0):

        self.load_data_panel = QGroupBox("Load data")
        self.load_data_layout = QGridLayout()
        self.load_data_layout.setSpacing(15)
        self.load_data_layout.setContentsMargins(10, 10, 10, 10)
        self.load_data_layout.setAlignment(QtCore.Qt.AlignBottom)

        self.signal_image_choice, _ = add_combobox(
            self.load_data_layout,
            "Signal image",
            self.image_layer_names,
            1,
            callback=self.set_signal_image,
        )
        self.background_image_choice, _ = add_combobox(
            self.load_data_layout,
            "Background image",
            self.image_layer_names,
            2,
            callback=self.set_background_image,
        )
        self.training_data_cell_choice, _ = add_combobox(
            self.load_data_layout,
            "Training data (cells)",
            self.point_layer_names,
            3,
            callback=self.set_training_data_cell,
        )
        self.training_data_non_cell_choice, _ = add_combobox(
            self.load_data_layout,
            "Training_data (non_cells)",
            self.point_layer_names,
            4,
            callback=self.set_training_data_non_cell,
        )
        self.add_training_data_button = add_button(
            "Add training data",
            self.load_data_layout,
            self.add_training_data,
            5,
        )
        self.extract_cube_button = add_button(
            "Extract cubes",
            self.load_data_layout,
            self.extract_cubes,
            5,
            column=1,
        )
        self.load_data_layout.setColumnMinimumWidth(0, COLUMN_WIDTH)
        self.load_data_panel.setLayout(self.load_data_layout)
        self.load_data_panel.setVisible(True)
        self.layout.addWidget(self.load_data_panel, row, column, 1, 1)

    def set_signal_image(self):
        if self.signal_image_choice.currentText() != "":
            self.signal_layer = self.viewer.layers[
                self.signal_image_choice.currentText()
            ]

    def set_background_image(self):
        if self.background_image_choice.currentText() != "":
            self.background_layer = self.viewer.layers[
                self.background_image_choice.currentText()
            ]

    def set_training_data_cell(self):
        if self.training_data_cell_choice.currentText() != "":
            self.training_data_cell_layer = self.viewer.layers[
                self.training_data_cell_choice.currentText()
            ]
            self.training_data_cell_layer.metadata["point_type"] = Cell.CELL
            self.training_data_cell_layer.metadata["training_data"] = True

    def set_training_data_non_cell(self):
        if self.training_data_non_cell_choice.currentText() != "":
            self.training_data_non_cell_layer = self.viewer.layers[
                self.training_data_non_cell_choice.currentText()
            ]
            self.training_data_non_cell_layer.metadata[
                "point_type"
            ] = Cell.UNKNOWN
            self.training_data_non_cell_layer.metadata["training_data"] = True

    def add_training_data(
        self,
        cell_name="Training data (cells)",
        non_cell_name="Training data (non cells)",
    ):
        self.training_data_cell_layer = self.viewer.add_points(
            np.empty((0, 3)),
            symbol="ring",
            n_dimensional=True,
            size=15,
            opacity=0.6,
            face_color="lightgoldenrodyellow",
            name=cell_name,
            metadata=dict(point_type=Cell.CELL, training_data=True),
        )
        self.training_data_cell_layer = self.viewer.add_points(
            np.empty((0, 3)),
            symbol="ring",
            n_dimensional=True,
            size=15,
            opacity=0.6,
            face_color="lightskyblue",
            name=non_cell_name,
            metadata=dict(point_type=Cell.UNKNOWN, training_data=True),
        )
        self.training_data_cell_choice.setCurrentText(cell_name)
        self.training_data_non_cell_choice.setCurrentText(non_cell_name)

    def extract_cubes(self):
        if self.check_training_data_exists():

            self.get_output_directory()
            self.status_label.setText("Extracting cubes")
            self.convert_layers_to_cells()
            # to_extract = {
            #     "cells": self.cells_to_extract,
            #     "non_cells": self.non_cells_to_extract,
            # }

            #
            # for cell_type, cell_list in to_extract.items():
            #     print(f"Extracting type: {cell_type}")
            #     cell_type_output_directory = self.output_directory / cell_type
            #     print(f"Saving to: {cell_type_output_directory}")
            #     ensure_directory_exists(str(cell_type_output_directory))
            #     extract_cubes_main(
            #         cell_list,
            #         cell_type_output_directory,
            #         self.signal_layer.data,
            #         self.background_layer.data,
            #         self.cube_depth,
            #         self.cube_width,
            #         self.cube_height,
            #         voxel_sizes,
            #         self.network_voxel_sizes,
            #         max_ram,
            #         self.n_free_cpus,
            #         self.save_empty_cubes,
            #     )
            self.status_label.setText("Ready")

    def check_training_data_exists(self):
        if not (
            self.training_data_cell_layer or self.training_data_non_cell_layer
        ):
            display_info(
                self,
                "No training data",
                "No training data layers have been added. "
                "Please add a layer and annotate some points.",
            )
            return False
        else:
            if (
                len(self.training_data_cell_layer.data) > 0
                or len(self.training_data_non_cell_layer.data) > 0
            ):
                return True
            else:
                display_info(
                    self,
                    "No training data",
                    "No training data points have been added. "
                    "Please annotate some points.",
                )
                return False

    def get_output_directory(self):
        """
        Shows file dialog to choose output directory
        """
        self.status_label.setText("Setting output directory...")
        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog
        self.output_directory = QFileDialog.getExistingDirectory(
            self,
            "Select output directory",
            options=options,
        )
        self.output_directory = Path(self.output_directory)

    def convert_layers_to_cells(self):

        self.cells_to_extract = convert_layer_to_cells(
            self.training_data_cell_layer.data
        )
        self.non_cells_to_extract = convert_layer_to_cells(
            self.training_data_non_cell_layer.data, cells=False
        )
