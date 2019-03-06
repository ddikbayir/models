# Copyright 2017 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
"""Core run logic for TensorFlow Wide & Deep Tutorial using tf.estimator API."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import shutil

from absl import app as absl_app
from absl import flags
import tensorflow as tf  # pylint: disable=g-bad-import-order
import json

from tensorflow.core.framework import graph_pb2 as gpb
from google.protobuf import text_format as pbtf

from official.utils.flags import core as flags_core
from official.utils.logs import hooks_helper
from official.utils.logs import logger
from official.utils.misc import model_helpers


LOSS_PREFIX = {'wide': 'linear/', 'deep': 'dnn/'}


def define_wide_deep_flags():
  """Add supervised learning flags, as well as wide-deep model type."""
  flags_core.define_base()
  flags_core.define_benchmark()

  flags.adopt_module_key_flags(flags_core)

  flags.DEFINE_enum(
      name="model_type", short_name="mt", default="wide_deep",
      enum_values=['wide', 'deep', 'wide_deep'],
      help="Select model topology.")
  flags.DEFINE_boolean(
      name="download_if_missing", default=True, help=flags_core.help_wrap(
          "Download data to data_dir if it is not already present."))


def export_model(model, model_type, export_dir, model_column_fn):
  """Export to SavedModel format.

  Args:
    model: Estimator object
    model_type: string indicating model type. "wide", "deep" or "wide_deep"
    export_dir: directory to export the model.
    model_column_fn: Function to generate model feature columns.
  """
  wide_columns, deep_columns = model_column_fn()
  if model_type == 'wide':
    columns = wide_columns
  elif model_type == 'deep':
    columns = deep_columns
  else:
    columns = wide_columns + deep_columns
  feature_spec = tf.feature_column.make_parse_example_spec(columns)
  example_input_fn = (
      tf.estimator.export.build_parsing_serving_input_receiver_fn(feature_spec))
  model.export_savedmodel(export_dir, example_input_fn)


def run_loop(name, train_input_fn, eval_input_fn, model_column_fn,
             build_estimator_fn, flags_obj, tensors_to_log, early_stop=False):
  """Define training loop."""
  model_helpers.apply_clean(flags.FLAGS)
  model = build_estimator_fn(
      model_dir=flags_obj.model_dir, model_type=flags_obj.model_type,
      model_column_fn=model_column_fn)

  run_params = {
      'batch_size': flags_obj.batch_size,
      'train_epochs': flags_obj.train_epochs,
      'model_type': flags_obj.model_type,
  }

  benchmark_logger = logger.get_benchmark_logger()
  benchmark_logger.log_run_info('wide_deep', name, run_params,
                                test_id=flags_obj.benchmark_test_id)

  loss_prefix = LOSS_PREFIX.get(flags_obj.model_type, '')
  tensors_to_log = {k: v.format(loss_prefix=loss_prefix)
                    for k, v in tensors_to_log.items()}
  train_hooks = hooks_helper.get_train_hooks(
      flags_obj.hooks, model_dir=flags_obj.model_dir,
      batch_size=flags_obj.batch_size, tensors_to_log=tensors_to_log)

  profiler_hook = tf.train.ProfilerHook(save_steps= 100, save_secs= None, output_dir="profs", show_memory=True, show_dataflow=True)
  
  #DOGA DEBUG GRAPH
  gdef = gpb.GraphDef()
 
  with open('/tmp/census_model/graph.pbtxt', 'r') as fh:
      graph_str = fh.read()

  pbtf.Parse(graph_str, gdef)

  with tf.Graph().as_default() as graph:
      tf.import_graph_def(gdef)

      operations_tensors = {}
      operations_names = tf.get_default_graph().get_operations()
      count1 = 0
      count2 = 0

      for operation in operations_names:
          operation_name = operation.name
          operations_info = tf.get_default_graph().get_operation_by_name(operation_name).values()
          if len(operations_info) > 0:
              if not (operations_info[0].shape.ndims is None):
                  operation_shape = operations_info[0].shape.as_list()
                  operation_dtype_size = operations_info[0].dtype.size
                  if not (operation_dtype_size is None):
                      operation_no_of_elements = 1
                      for dim in operation_shape:
                          if not(dim is None):
                              operation_no_of_elements = operation_no_of_elements * dim
                      total_size = operation_no_of_elements * operation_dtype_size
                      operations_tensors[operation_name] = total_size
                  else:
                      count1 = count1 + 1
              else:
                  count1 = count1 + 1
                  operations_tensors[operation_name] = -1
          else:
              count2 = count2 + 1
              operations_tensors[operation_name] = -1

      print(count1)
      print(count2)

  with open('tensors_sz.json', 'w') as f:
      json.dump(operations_tensors, f)
  
  # Train and evaluate the model every `flags.epochs_between_evals` epochs.
  for n in range(flags_obj.train_epochs // flags_obj.epochs_between_evals):
      model.train(input_fn=train_input_fn, hooks=[profiler_hook])

      results = model.evaluate(input_fn=eval_input_fn)

      # Display evaluation metrics
      tf.logging.info('Results at epoch %d / %d',
                      (n + 1) * flags_obj.epochs_between_evals,
                      flags_obj.train_epochs)
      tf.logging.info('-' * 60)

      for key in sorted(results):
        tf.logging.info('%s: %s' % (key, results[key]))

      benchmark_logger.log_evaluation_result(results)

      if early_stop and model_helpers.past_stop_threshold(
          flags_obj.stop_threshold, results['accuracy']):
        break

  # Export the model
  if flags_obj.export_dir is not None:
      export_model(model, flags_obj.model_type, flags_obj.export_dir,
                   model_column_fn)
