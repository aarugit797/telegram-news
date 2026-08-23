#!/bin/bash
# This runs automatically when the Postgres container first initializes.
# POSTGRES_DB only creates ONE database (techintel_news), so this script
# creates the second one (techintel_conversation) we need for our
# separate Conversation DB, in the same Postgres instance.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE techintel_conversation;
EOSQL
