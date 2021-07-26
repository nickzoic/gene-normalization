"""This module creates the database."""
from gene import PREFIX_LOOKUP
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from os import environ
from typing import List, Optional, Dict, Any
import boto3
import click
import sys
import logging


logger = logging.getLogger()
logger.setLevel(logging.DEBUG)


class Database:
    """The database class."""

    def __init__(self, db_url: str = '', region_name: str = 'us-east-2'):
        """Initialize Database class.

        :param str db_url: URL endpoint for DynamoDB source
        :param str region_name: default AWS region
        """
        if 'GENE_NORM_PROD' in environ or 'GENE_NORM_EB_PROD' in environ:
            boto_params = {
                'region_name': region_name
            }
            if 'GENE_NORM_EB_PROD' not in environ:
                # EB Instance should not have to confirm.
                # This is used only for updating production via CLI
                if click.confirm("Are you sure you want to use the "
                                 "production database?", default=False):
                    click.echo("***GENE PRODUCTION DATABASE IN USE***")
                else:
                    click.echo("Exiting.")
                    sys.exit()
        else:
            if db_url:
                endpoint_url = db_url
            elif 'GENE_NORM_DB_URL' in environ:
                endpoint_url = environ['GENE_NORM_DB_URL']
            else:
                endpoint_url = 'http://localhost:8000'
            click.echo(f"***Using Gene Database Endpoint: {endpoint_url}***")
            boto_params = {
                'region_name': region_name,
                'endpoint_url': endpoint_url
            }

        self.dynamodb = boto3.resource('dynamodb', **boto_params)
        self.dynamodb_client = boto3.client('dynamodb', **boto_params)

        # Create tables if nonexistent if not connecting to production database
        if 'GENE_NORM_PROD' not in environ and\
                'GENE_NORM_EB_PROD' not in environ and 'TEST' not in environ:
            self.create_db_tables()

        self.genes = self.dynamodb.Table('gene_concepts')
        self.metadata = self.dynamodb.Table('gene_metadata')
        self.batch = self.genes.batch_writer()
        self.cached_sources = {}

    def _get_table_names(self) -> List[str]:
        """Return names of tables in database.

        :return: Table names in DynamoDB
        """
        return self.dynamodb_client.list_tables()['TableNames']

    def delete_all_db_tables(self) -> None:
        """Delete all tables from database."""
        existing_tables = self._get_table_names()
        for table_name in existing_tables:
            self.dynamodb.Table(table_name).delete()

    def create_db_tables(self) -> None:
        """Create gene_concepts and gene_metadata tables."""
        existing_tables = self._get_table_names()
        self.create_genes_table(existing_tables)
        self.create_meta_data_table(existing_tables)

    def create_genes_table(self, existing_tables: List[str]):
        """Create Genes table if non-existent.

        :param List[str] existing_tables: table names already in DB
        """
        table_name = 'gene_concepts'
        if table_name not in existing_tables:
            self.dynamodb.create_table(
                TableName=table_name,
                KeySchema=[
                    {
                        'AttributeName': 'label_and_type',
                        'KeyType': 'HASH'  # Partition key
                    },
                    {
                        'AttributeName': 'concept_id',
                        'KeyType': 'RANGE'  # Sort key
                    }
                ],
                AttributeDefinitions=[
                    {
                        'AttributeName': 'label_and_type',
                        'AttributeType': 'S'
                    },
                    {
                        'AttributeName': 'concept_id',
                        'AttributeType': 'S'
                    },
                    {
                        'AttributeName': 'src_name',
                        'AttributeType': 'S'
                    },
                    {
                        'AttributeName': 'item_type',
                        'AttributeType': 'S'
                    }

                ],
                GlobalSecondaryIndexes=[
                    {
                        'IndexName': 'src_index',
                        'KeySchema': [
                            {
                                'AttributeName': 'src_name',
                                'KeyType': 'HASH'
                            }
                        ],
                        'Projection': {
                            'ProjectionType': 'KEYS_ONLY'
                        },
                        'ProvisionedThroughput': {
                            'ReadCapacityUnits': 10,
                            'WriteCapacityUnits': 10
                        }
                    },
                    {
                        'IndexName': 'item_type_index',
                        'KeySchema': [
                            {
                                'AttributeName': 'item_type',
                                'KeyType': 'HASH'
                            }
                        ],
                        'Projection': {
                            'ProjectionType': 'KEYS_ONLY'
                        },
                        'ProvisionedThroughput': {
                            'ReadCapacityUnits': 10,
                            'WriteCapacityUnits': 10
                        }
                    }
                ],
                ProvisionedThroughput={
                    'ReadCapacityUnits': 10,
                    'WriteCapacityUnits': 10
                }
            )

    def create_meta_data_table(self, existing_tables: List[str]):
        """Create MetaData table if non-existent.

        :param List[str] existing_tables: table names already in DB
        """
        table_name = 'gene_metadata'
        if table_name not in existing_tables:
            self.dynamodb.create_table(
                TableName=table_name,
                KeySchema=[
                    {
                        'AttributeName': 'src_name',
                        'KeyType': 'HASH'  # Partition key
                    }
                ],
                AttributeDefinitions=[
                    {
                        'AttributeName': 'src_name',
                        'AttributeType': 'S'
                    },
                ],
                ProvisionedThroughput={
                    'ReadCapacityUnits': 10,
                    'WriteCapacityUnits': 10
                }
            )

    def get_record_by_id(self, concept_id: str,
                         case_sensitive: bool = True,
                         merge: bool = False) -> Optional[Dict]:
        """Fetch record corresponding to provided concept ID
        :param str concept_id: concept ID for gene record
        :param bool case_sensitive: if true, performs exact lookup, which is
            more efficient. Otherwise, performs filter operation, which
            doesn't require correct casing.
        :param bool merge: if true, look for merged record; look for identity
            record otherwise.
        :return: complete gene record, if match is found; None otherwise
        """
        try:
            if merge:
                pk = f'{concept_id.lower()}##merger'
            else:
                pk = f'{concept_id.lower()}##identity'
            if case_sensitive:
                match = self.genes.get_item(Key={
                    'label_and_type': pk,
                    'concept_id': concept_id
                })
                return match['Item']
            else:
                exp = Key('label_and_type').eq(pk)
                response = self.genes.query(KeyConditionExpression=exp)
                return response['Items'][0]
        except ClientError as e:
            logger.error(f"boto3 client error on get_records_by_id for "
                         f"search term {concept_id}: "
                         f"{e.response['Error']['Message']}")
            return None
        except (KeyError, IndexError):  # record doesn't exist
            return None

    def get_records_by_type(self, query: str,
                            match_type: str) -> List[Dict]:
        """Retrieve records for given query and match type.
        :param query: string to match against
        :param str match_type: type of match to look for. Should be one
            of {"label", "alias", "xref", "associated_with"} (use
            `get_record_by_id` for concept ID lookup)
        :return: list of matching records. Empty if lookup fails.
        """
        pk = f'{query}##{match_type.lower()}'
        filter_exp = Key('label_and_type').eq(pk)
        try:
            matches = self.genes.query(KeyConditionExpression=filter_exp)
            return matches.get('Items', None)
        except ClientError as e:
            logger.error(f"boto3 client error on get_records_by_type for "
                         f"search term {query}: "
                         f"{e.response['Error']['Message']}")
            return []

    def add_record(self, record: Dict, record_type: str = "identity"):
        """Add new record to database.
        :param Dict record: record to upload
        :param str record_type: type of record (either 'identity' or 'merger')
        """
        id_prefix = record['concept_id'].split(':')[0].lower()
        record['src_name'] = PREFIX_LOOKUP[id_prefix]
        label_and_type = f'{record["concept_id"].lower()}##{record_type}'
        record['label_and_type'] = label_and_type
        record['item_type'] = record_type
        try:
            self.genes.put_item(
                Item=record,
                ConditionExpression='attribute_not_exists(concept_id) AND attribute_not_exists(label_and_type)'  # noqa: E501
            )
        except ClientError as e:
            logger.error("boto3 client error on add_record for "
                         f"{record['concept_id']}: "
                         f"{e.response['Error']['Message']}")

    def add_ref_record(self, term: str, concept_id: str, ref_type: str):
        """Add auxiliary/reference record to database.
        :param str term: referent term
        :param str concept_id: concept ID to refer to
        :param str ref_type: one of {'alias', 'label', 'xref',
            'associated_with'}
        """
        label_and_type = f'{term.lower()}##{ref_type}'
        src_name = PREFIX_LOOKUP[concept_id.split(':')[0].lower()]
        record = {
            'label_and_type': label_and_type,
            'concept_id': concept_id.lower(),
            'src_name': src_name,
            'item_type': ref_type,
        }
        try:
            self.batch.put_item(Item=record)
        except ClientError as e:
            logger.error(f"boto3 client error adding reference {term} for "
                         f"{concept_id} with match type {ref_type}: "
                         f"{e.response['Error']['Message']}")

    def update_record(self, concept_id: str, field: str, new_value: Any,
                      item_type: str = 'identity'):
        """Update the field of an individual record to a new value.
        :param str concept_id: record to update
        :param str field: name of field to update
        :param str new_value: new value
        :param str item_type: record type, one of {'identity', 'merger'}
        """
        key = {
            'label_and_type': f'{concept_id.lower()}##{item_type}',
            'concept_id': concept_id
        }
        update_expression = f"set {field}=:r"
        update_values = {':r': new_value}
        try:
            self.genes.update_item(Key=key,
                                   UpdateExpression=update_expression,
                                   ExpressionAttributeValues=update_values)
        except ClientError as e:
            logger.error(f"boto3 client error in `database.update_record()`: "
                         f"{e.response['Error']['Message']}")

    def flush_batch(self):
        """Flush internal batch_writer."""
        self.batch.__exit__(*sys.exc_info())
        self.batch = self.genes.batch_writer()
