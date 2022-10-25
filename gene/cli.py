"""This module provides a CLI util to make updates to normalizer database."""
import logging
from os import environ
from timeit import default_timer as timer

import click
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key

from gene import SOURCES
from gene.database import Database, confirm_aws_db_use, SKIP_AWS_DB_ENV_NAME, \
    VALID_AWS_ENV_NAMES, AWS_ENV_VAR_NAME
from gene.etl import NCBI, HGNC, Ensembl  # noqa: F401
from gene.etl.merge import Merge
from gene.schemas import SourceName


logger = logging.getLogger('gene')
logger.setLevel(logging.DEBUG)


class CLI:
    """Class for updating the normalizer database via Click"""

    @staticmethod
    @click.command()
    @click.option(
        '--normalizer',
        help="The normalizer(s) you wish to update separated by spaces."
    )
    @click.option(
        '--aws_instance',
        is_flag=True,
        help="Using AWS DynamodDB instance."
    )
    @click.option(
        '--db_url',
        help="URL endpoint for the application database."
    )
    @click.option(
        '--update_all',
        is_flag=True,
        help='Update all normalizer sources.'
    )
    @click.option(
        '--update_merged',
        is_flag=True,
        help='Update concepts for normalize endpoint from accepted sources.'
    )
    def update_normalizer_db(normalizer, aws_instance, db_url, update_all,
                             update_merged):
        """Update selected normalizer source(s) in the gene database."""
        # If SKIP_AWS_CONFIRMATION is accidentally set, we should verify that the
        # aws instance should actually be used
        invalid_aws_msg = f"{AWS_ENV_VAR_NAME} must be set to one of {VALID_AWS_ENV_NAMES}"  # noqa: E501
        aws_env_var_set = False
        if AWS_ENV_VAR_NAME in environ:
            aws_env_var_set = True
            assert environ[AWS_ENV_VAR_NAME] in VALID_AWS_ENV_NAMES, invalid_aws_msg
            confirm_aws_db_use(environ[AWS_ENV_VAR_NAME].upper())

        if aws_env_var_set or aws_instance:
            assert AWS_ENV_VAR_NAME in environ, invalid_aws_msg
            environ[SKIP_AWS_DB_ENV_NAME] = "true"  # this is already checked above
            db: Database = Database()
        else:
            if db_url:
                endpoint_url = db_url
            elif 'GENE_NORM_DB_URL' in environ.keys():
                endpoint_url = environ['GENE_NORM_DB_URL']
            else:
                endpoint_url = 'http://localhost:8000'
            db: Database = Database(db_url=endpoint_url)

        if update_all:
            normalizers = [src for src in SOURCES]
            CLI()._update_normalizers(normalizers, db, update_merged)
        elif not normalizer:
            if update_merged:
                CLI()._load_merge(db, [])
            else:
                CLI()._help_msg()
        else:
            normalizers = normalizer.lower().split()

            if len(normalizers) == 0:
                raise Exception("Must enter a normalizer")

            non_sources = set(normalizers) - {src for src in SOURCES}

            if len(non_sources) != 0:
                raise Exception(f"Not valid source(s): {non_sources}")

            CLI()._update_normalizers(normalizers, db, update_merged)

    @staticmethod
    def _help_msg():
        """Display help message."""
        ctx = click.get_current_context()
        click.echo("Must either enter 1 or more sources, or use `--update_all` parameter")  # noqa: E501
        click.echo(ctx.get_help())
        ctx.exit()

    @staticmethod
    def _update_normalizers(normalizers, db, update_merged):
        """Update selected normalizer sources."""
        processed_ids = list()
        for n in normalizers:
            delete_time = CLI()._delete_source(n, db)
            CLI()._load_source(n, db, delete_time, processed_ids)

        if update_merged:
            CLI()._load_merge(db, processed_ids)

    @staticmethod
    def _delete_source(n, db):
        """Delete individual source data."""
        msg = f"Deleting {n}..."
        click.echo(f"\n{msg}")
        logger.info(msg)
        start_delete = timer()
        CLI()._delete_data(n, db)
        end_delete = timer()
        delete_time = end_delete - start_delete
        msg = f"Deleted {n} in {delete_time:.5f} seconds."
        click.echo(f"{msg}\n")
        logger.info(msg)
        return delete_time

    @staticmethod
    def _load_source(n, db, delete_time, processed_ids):
        """Load individual source data."""
        msg = f"Loading {n}..."
        click.echo(msg)
        logger.info(msg)
        start_load = timer()

        # used to get source class name from string
        SOURCES_CLASS = \
            {s.value.lower(): eval(s.value) for s in SourceName.__members__.values()}

        source = SOURCES_CLASS[n](database=db)
        processed_ids += source.perform_etl()
        end_load = timer()
        load_time = end_load - start_load
        msg = f"Loaded {n} in {load_time:.5f} seconds."
        click.echo(msg)
        logger.info(msg)
        msg = f"Total time for {n}: {(delete_time + load_time):.5f} seconds."
        click.echo(msg)
        logger.info(msg)

    @staticmethod
    def _load_merge(db, processed_ids):
        """Load merged concepts"""
        start = timer()
        if not processed_ids:
            CLI()._delete_normalized_data(db)
            processed_ids = db.get_ids_for_merge()
        merge = Merge(database=db)
        click.echo("Constructing normalized records...")
        merge.create_merged_concepts(processed_ids)
        end = timer()
        click.echo(f"Merged concept generation completed in "
                   f"{(end - start):.5f} seconds")

    @staticmethod
    def _delete_normalized_data(database):
        """Delete normalized concepts"""
        click.echo("\nDeleting normalized records...")
        start_delete = timer()
        try:
            while True:
                with database.genes.batch_writer(
                        overwrite_by_pkeys=['label_and_type', 'concept_id']) \
                        as batch:
                    response = database.genes.query(
                        IndexName='item_type_index',
                        KeyConditionExpression=Key('item_type').eq('merger'),
                    )
                    records = response['Items']
                    if not records:
                        break
                    for record in records:
                        batch.delete_item(Key={
                            'label_and_type': record['label_and_type'],
                            'concept_id': record['concept_id']
                        })
        except ClientError as e:
            click.echo(e.response['Error']['Message'])
        end_delete = timer()
        delete_time = end_delete - start_delete
        click.echo(f"Deleted normalized records in {delete_time:.5f} seconds.")

    @staticmethod
    def _delete_data(source, database):
        """Delete a source's data from dynamodb table."""
        # Delete source's metadata
        try:
            metadata = database.metadata.query(
                KeyConditionExpression=Key(
                    'src_name').eq(SourceName[f"{source.upper()}"].value)
            )
            if metadata['Items']:
                database.metadata.delete_item(
                    Key={'src_name': metadata['Items'][0]['src_name']},
                    ConditionExpression="src_name = :src",
                    ExpressionAttributeValues={
                        ':src': SourceName[f"{source.upper()}"].value}
                )
        except ClientError as e:
            click.echo(e.response['Error']['Message'])

        # Delete source's data from genes table
        try:
            while True:
                response = database.genes.query(
                    IndexName='src_index',
                    KeyConditionExpression=Key('src_name').eq(
                        SourceName[f"{source.upper()}"].value)
                )

                records = response['Items']
                if not records:
                    break

                with database.genes.batch_writer(
                        overwrite_by_pkeys=['label_and_type', 'concept_id']) \
                        as batch:

                    for record in records:
                        batch.delete_item(
                            Key={
                                'label_and_type': record['label_and_type'],
                                'concept_id': record['concept_id']
                            }
                        )
        except ClientError as e:
            click.echo(e.response['Error']['Message'])


if __name__ == '__main__':
    CLI().update_normalizer_db()
