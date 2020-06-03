#
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
import os
import re
import unittest

from airflow.operators.hive_to_mysql import HiveToMySqlTransfer
from tests.compat import MagicMock, PropertyMock, patch

from airflow.utils import timezone
from airflow.utils.operator_helpers import context_to_airflow_vars
from tests.hive import TestHiveEnvironment

from tests.test_utils.mock_hooks import MockHiveServer2Hook, MockMySqlHook

DEFAULT_DATE = timezone.datetime(2015, 1, 1)


class TestHiveToMySqlTransfer(TestHiveEnvironment):

    def setUp(self):
        self.kwargs = dict(
            sql='sql',
            mysql_table='table',
            hiveserver2_conn_id='hiveserver2_default',
            mysql_conn_id='mysql_default',
            task_id='test_hive_to_mysql',
        )
        super(TestHiveToMySqlTransfer, self).setUp()

    @patch('airflow.operators.hive_to_mysql.MySqlHook')
    @patch('airflow.operators.hive_to_mysql.HiveServer2Hook')
    def test_execute(self, mock_hive_hook, mock_mysql_hook):
        HiveToMySqlTransfer(**self.kwargs).execute(context={})

        mock_hive_hook.assert_called_once_with(hiveserver2_conn_id=self.kwargs['hiveserver2_conn_id'])
        mock_hive_hook.return_value.get_records.assert_called_once_with('sql')
        mock_mysql_hook.assert_called_once_with(mysql_conn_id=self.kwargs['mysql_conn_id'])
        mock_mysql_hook.return_value.insert_rows.assert_called_once_with(
            table=self.kwargs['mysql_table'],
            rows=mock_hive_hook.return_value.get_records.return_value
        )

    @patch('airflow.operators.hive_to_mysql.MySqlHook')
    @patch('airflow.operators.hive_to_mysql.HiveServer2Hook')
    def test_execute_mysql_preoperator(self, mock_hive_hook, mock_mysql_hook):
        self.kwargs.update(dict(mysql_preoperator='preoperator'))

        HiveToMySqlTransfer(**self.kwargs).execute(context={})

        mock_mysql_hook.return_value.run.assert_called_once_with(self.kwargs['mysql_preoperator'])

    @patch('airflow.operators.hive_to_mysql.MySqlHook')
    @patch('airflow.operators.hive_to_mysql.HiveServer2Hook')
    def test_execute_with_mysql_postoperator(self, mock_hive_hook, mock_mysql_hook):
        self.kwargs.update(dict(mysql_postoperator='postoperator'))

        HiveToMySqlTransfer(**self.kwargs).execute(context={})

        mock_mysql_hook.return_value.run.assert_called_once_with(self.kwargs['mysql_postoperator'])

    @patch('airflow.operators.hive_to_mysql.MySqlHook')
    @patch('airflow.operators.hive_to_mysql.NamedTemporaryFile')
    @patch('airflow.operators.hive_to_mysql.HiveServer2Hook')
    def test_execute_bulk_load(self, mock_hive_hook, mock_tmp_file, mock_mysql_hook):
        type(mock_tmp_file).name = PropertyMock(return_value='tmp_file')
        context = {}
        self.kwargs.update(dict(bulk_load=True))

        HiveToMySqlTransfer(**self.kwargs).execute(context=context)

        mock_tmp_file.assert_called_once_with()
        mock_hive_hook.return_value.to_csv.assert_called_once_with(
            self.kwargs['sql'],
            mock_tmp_file.return_value.name,
            delimiter='\t',
            lineterminator='\n',
            output_header=False,
            hive_conf=context_to_airflow_vars(context)
        )
        mock_mysql_hook.return_value.bulk_load.assert_called_once_with(
            table=self.kwargs['mysql_table'],
            tmp_file=mock_tmp_file.return_value.name
        )
        mock_tmp_file.return_value.close.assert_called_once_with()

    @unittest.skipIf(
        'AIRFLOW_RUNALL_TESTS' not in os.environ,
        "Skipped because AIRFLOW_RUNALL_TESTS is not set")
    def test_hive_to_mysql(self):
        test_hive_results = 'test_hive_results'

        mock_hive_hook = MockHiveServer2Hook()
        mock_hive_hook.get_records = MagicMock(return_value=test_hive_results)

        mock_mysql_hook = MockMySqlHook()
        mock_mysql_hook.run = MagicMock()
        mock_mysql_hook.insert_rows = MagicMock()

        with patch('airflow.operators.hive_to_mysql.HiveServer2Hook',
                   return_value=mock_hive_hook):
            with patch('airflow.operators.hive_to_mysql.MySqlHook',
                       return_value=mock_mysql_hook):

                op = HiveToMySqlTransfer(
                    mysql_conn_id='airflow_db',
                    task_id='hive_to_mysql_check',
                    sql="""
                        SELECT name
                        FROM airflow.static_babynames
                        LIMIT 100
                        """,
                    mysql_table='test_static_babynames',
                    mysql_preoperator=[
                        'DROP TABLE IF EXISTS test_static_babynames;',
                        'CREATE TABLE test_static_babynames (name VARCHAR(500))',
                    ],
                    dag=self.dag)
                op.clear(start_date=DEFAULT_DATE, end_date=DEFAULT_DATE)
                op.run(start_date=DEFAULT_DATE, end_date=DEFAULT_DATE,
                       ignore_ti_state=True)

        raw_select_name_query = mock_hive_hook.get_records.call_args_list[0][0][0]
        actual_select_name_query = re.sub(r'\s{2,}', ' ', raw_select_name_query).strip()
        expected_select_name_query = 'SELECT name FROM airflow.static_babynames LIMIT 100'
        self.assertEqual(expected_select_name_query, actual_select_name_query)

        actual_hive_conf = mock_hive_hook.get_records.call_args_list[0][1]['hive_conf']
        expected_hive_conf = {'airflow.ctx.dag_owner': 'airflow',
                              'airflow.ctx.dag_id': 'test_dag_id',
                              'airflow.ctx.task_id': 'hive_to_mysql_check',
                              'airflow.ctx.execution_date': '2015-01-01T00:00:00+00:00'}
        self.assertEqual(expected_hive_conf, actual_hive_conf)

        expected_mysql_preoperator = ['DROP TABLE IF EXISTS test_static_babynames;',
                                      'CREATE TABLE test_static_babynames (name VARCHAR(500))']
        mock_mysql_hook.run.assert_called_with(expected_mysql_preoperator)

        mock_mysql_hook.insert_rows.assert_called_with(table='test_static_babynames', rows=test_hive_results)
