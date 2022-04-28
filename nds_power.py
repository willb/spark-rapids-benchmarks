# -*- coding: utf-8 -*-
#
# SPDX-FileCopyrightText: Copyright (c) 2022 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
#
# -----
#
# Certain portions of the contents of this file are derived from TPC-DS version 3.2.0
# (retrieved from www.tpc.org/tpc_documents_current_versions/current_specifications5.asp).
# Such portions are subject to copyrights held by Transaction Processing Performance Council (“TPC”)
# and licensed under the TPC EULA (a copy of which accompanies this file as “TPC EULA” and is also
# available at http://www.tpc.org/tpc_documents_current_versions/current_specifications5.asp) (the “TPC EULA”).
#
# You may not use this file except in compliance with the TPC EULA.
# DISCLAIMER: Portions of this file is derived from the TPC-DS Benchmark and as such any results
# obtained using this file are not comparable to published TPC-DS Benchmark results, as the results
# obtained from using this file do not comply with the TPC-DS Benchmark.
#

import argparse
import csv
import time
from pyspark.sql import SparkSession

from check import check_version
from nds_transcode import get_schemas

check_version()


def gen_sql_from_stream(query_stream_file_path):
    """Read Spark compatible query stream and split them one by one

    Args:
        query_stream_file_path (str): path of query stream generated by TPC-DS tool

    Returns:
        list of string: a list of all spark runnable queries
    """
    with open(query_stream_file_path, 'r') as f:
        stream = f.read()
    all_queries = stream.split('-- start')[1:]
    # split query in query14, query23, query24, query39
    extended_queries = []
    for q in all_queries:
        if 'select' in q.split(';')[1]:
            split_q = q.split(';')
            # now split_q has 3 items:
            # 1. "query x in stream x using template query[xx].tpl query_part_1"
            # 2. "query_part_2"
            # 3. "-- end query [x] in stream [x] using template query[xx].tpl"
            part_1 = split_q[0].replace('.tpl', '_part1.tpl')
            part_1 += ';'
            extended_queries.append(part_1)
            head = split_q[0].split('\n')[0]
            part_2 = head.replace('.tpl', '_part2.tpl') + '\n'
            part_2 += split_q[1]
            part_2 += ';'
            extended_queries.append(part_2)
        else:
            extended_queries.append(q)

    # add "-- start" string back to each query
    extended_queries = ['-- start' + q for q in extended_queries]
    return extended_queries


def run_query_stream(input_prefix,
                     query_list,
                     time_log_output_path,
                     output_path=None,
                     output_format="parquet"):
    """run SQL in Spark and record execution time log. The execution time log is saved as a CSV file
    for easy accesibility. TempView Creation time is also recorded

    Args:
        input_prefix (str): path of input data
        query_list (list of str): list of all TPC-DS queries runnable in Spark
        time_log_output_path (str): path of the log that contains query execution time, both local
                                    and HDFS path are supported.
        output_path (str, optional): path of query output, optinal. If not specified, collect()
                                     action will be applied to each query . Defaults to None.
        output_format (str, optional): query output format, choices are csv, orc, parquet. Defaults
        to "parquet".
    """
    execution_time_list = []
    total_time_start = time.time()
    # Execute Power Run in Spark
    # build Spark Session
    spark_session = SparkSession.builder.appName(
        "NDS - Power Run").getOrCreate()
    spark_app_id = spark_session.sparkContext.applicationId
    # Create TempView for tables
    load_start = time.time()
    for table_name in get_schemas(False).keys():
        start = time.time()
        table_path = input_prefix + '/' + table_name
        spark_session.read.parquet(
            table_path).createOrReplaceTempView(table_name)
        end = time.time()
        print("====== Creating TempView for table {} ======".format(table_name))
        print("Time taken: {} s for table {}".format(end - start, table_name))
        execution_time_list.append(
            (spark_app_id, "CreateTempView {}".format(table_name), end - start))
    load_end = time.time()
    load_elapse = load_end - load_start
    print("Load Time: {} s for all tables".format(load_end - load_start))
    execution_time_list.append((spark_app_id, "Load Time", load_elapse))
    # Run query
    power_start = time.time()
    for q in query_list:
        df = spark_session.sql(q)
        # e.g. "-- start query 32 in stream 0 using template query98.tpl"
        query_name = q[q.find('template')+9: q.find('.tpl')]
        # show query name in Spark web UI
        spark_session.sparkContext.setJobGroup(query_name, query_name)
        print("====== Run {} ======".format(query_name))
        start = time.time()
        if not output_path:
            df.collect()
        else:
            df.write.format(output_format).mode('overwrite').save(
                output_path + '/' + query_name)
        end = time.time()

        print("Time taken: {} s for {}".format(end-start, query_name))
        execution_time_list.append((spark_app_id, query_name, end-start))
    total_time_end = time.time()
    power_elapse = total_time_end - power_start
    total_elapse = total_time_end - total_time_start
    print("====== Power Test Time: {} s ======".format(power_elapse))
    print("====== Total Time: {} s ======".format(total_elapse))
    execution_time_list.append(
        (spark_app_id, "Power Test Time", power_elapse))
    execution_time_list.append(
        (spark_app_id, "Total Time", total_time_end - total_time_start))

    # write to local csv file
    header = ["application_id", "query", "time/s"]
    with open(time_log_output_path, 'w', encoding='UTF8') as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(execution_time_list)


if __name__ == "__main__":
    parser = parser = argparse.ArgumentParser()
    parser.add_argument('input_prefix',
                        help='text to prepend to every input file path (e.g., "hdfs:///ds-generated-data")')
    parser.add_argument('query_stream_file',
                        help='query stream file that contains NDS queries in specific order')
    parser.add_argument('time_log',
                        help='path to execution time log, only support local path.',
                        default="")
    parser.add_argument('--output_prefix',
                        help='text to prepend to every output file (e.g., "hdfs:///ds-parquet")')
    parser.add_argument('--output_format',
                        help='type of query output',
                        default='parquet')

    args = parser.parse_args()
    query_list = gen_sql_from_stream(args.query_stream_file)
    run_query_stream(args.input_prefix,
                     query_list,
                     args.time_log,
                     args.output_prefix,
                     args.output_format)
