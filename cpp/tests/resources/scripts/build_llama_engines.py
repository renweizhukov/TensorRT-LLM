#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2022-2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse as _arg
import pathlib as _pl
import platform as _pf
import sys as _sys

from build_engines_utils import init_model_spec_module, run_command, wincopy

init_model_spec_module()
import model_spec

import tensorrt_llm.bindings as _tb


def build_engine(weight_dir: _pl.Path, engine_dir: _pl.Path, *args):

    ckpt_dir = engine_dir / 'ckpt'

    covert_cmd = [_sys.executable, "examples/llama/convert_checkpoint.py"
                  ] + ([f'--model_dir={weight_dir}'] if weight_dir else []) + [
                      f'--output_dir={ckpt_dir}',
                      '--dtype=float16',
                  ] + list(args)

    run_command(covert_cmd)

    build_args = [
        'trtllm-build',
        f'--checkpoint_dir={ckpt_dir}',
        f'--output_dir={engine_dir}',
        '--gpt_attention_plugin=float16',
        '--gemm_plugin=float16',
        '--max_batch_size=32',
        '--max_input_len=40',
        '--max_seq_len=60',
        '--max_beam_width=2',
        '--log_level=error',
        '--paged_kv_cache=enable',
        '--remove_input_padding=enable',
    ]

    run_command(build_args)


def build_engines(model_cache: str, only_multi_gpu: bool):
    resources_dir = _pl.Path(__file__).parent.resolve().parent
    models_dir = resources_dir / 'models'
    model_name = 'llama-7b-hf'

    if model_cache:
        print("Copy model from model_cache")
        model_cache_dir = _pl.Path(model_cache) / 'llama-models' / model_name
        assert (model_cache_dir.is_dir()), model_cache_dir

        if _pf.system() == "Windows":
            wincopy(source=str(model_cache_dir),
                    dest=model_name,
                    isdir=True,
                    cwd=models_dir)
        else:
            run_command(
                ["rsync", "-av", str(model_cache_dir), "."], cwd=models_dir)

    hf_dir = models_dir / model_name
    assert hf_dir.is_dir()

    engine_dir = models_dir / 'rt_engine' / model_name

    model_spec_obj = model_spec.ModelSpec('input_tokens.npy', _tb.DataType.HALF)
    model_spec_obj.use_gpt_plugin()
    model_spec_obj.set_kv_cache_type(model_spec.KVCacheType.PAGED)
    model_spec_obj.use_packed_input()

    tp_pp_sizes = [(1, 1)]
    if only_multi_gpu:
        tp_pp_sizes = [(1, 4), (4, 1), (1, 2), (2, 2)]
    for tp_size, pp_size in tp_pp_sizes:
        tp_pp_dir = f"tp{tp_size}-pp{pp_size}-gpu"
        print(f"\nBuilding fp16 tp{tp_size} pp{pp_size} engine")
        model_spec_obj.use_tensor_parallelism(tp_size)
        model_spec_obj.use_pipeline_parallelism(pp_size)

        build_engine(hf_dir,
                     engine_dir / model_spec_obj.get_model_path() / tp_pp_dir,
                     f'--tp_size={tp_size}', f'--pp_size={pp_size}')

    print("Done.")


if __name__ == "__main__":
    parser = _arg.ArgumentParser()
    parser.add_argument("--model_cache",
                        type=str,
                        help="Directory where models are stored")
    parser.add_argument(
        "--only_multi_gpu",
        action="store_true",
        help="Flag to build only for Tensor and Pipeline parallelism")

    build_engines(**vars(parser.parse_args()))
