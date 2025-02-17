#!/bin/bash

set -e
set -x

DAYS=$((365*10))
OPENSSL=${OPENSSL:-"openssl"}

# CA
$OPENSSL ecparam -genkey -name P-256 -noout -out tenant-ca/tenant.ca.key
cat > tenant-ca/cert.conf <<EOF
[req]
distinguished_name = req_distinguished_name
prompt = no

[req_distinguished_name]
commonName=My CA
organizationName=My Organization
organizationalUnitName=My Unit
emailAddress=myusername@example.com
countryName=NO
localityName=Oslo
stateOrProvinceName=Oslo
EOF
$OPENSSL req -new -x509 -key tenant-ca/tenant.ca.key -out tenant-ca/tenant.ca.crt -config tenant-ca/cert.conf -days $DAYS

# Server
$OPENSSL ecparam -genkey -name P-256 -noout -out server/server.key
cat > server/cert.conf <<EOF
[req]
distinguished_name = req_distinguished_name
prompt = no

[req_distinguished_name]
commonName=my-server.com
organizationName=My Organization
organizationalUnitName=My Unit
emailAddress=myusername@example.com
countryName=NO
localityName=Oslo
stateOrProvinceName=Oslo
subjectAltName=DNS:my-server.com
EOF
$OPENSSL req -new -key server/server.key -out server/server.req -config server/cert.conf
$OPENSSL x509 -req -CA tenant-ca/tenant.ca.crt -CAkey tenant-ca/tenant.ca.key -CAcreateserial -in server/server.req -out server/server.crt -days $DAYS

# client 1 - ec256
$OPENSSL ecparam -genkey -name prime256v1 -noout -out client/client.1.ec256.key
$OPENSSL ec -in client/client.1.ec256.key -pubout -out client/client.1.ec256.public.key
cat > client/cert.conf <<EOF
[req]
distinguished_name = req_distinguished_name
prompt = no

[req_distinguished_name]
commonName=my-device-hostname.com
organizationName=My Organization
organizationalUnitName=My Unit
emailAddress=myusername@example.com
countryName=NO
localityName=Oslo
stateOrProvinceName=Oslo
EOF
$OPENSSL req -new -key client/client.1.ec256.key -out client/client.1.ec256.req -config client/cert.conf
$OPENSSL x509 -req -CA tenant-ca/tenant.ca.crt -CAkey tenant-ca/tenant.ca.key -CAcreateserial -in client/client.1.ec256.req -out client/client.1.ec256.crt -days $DAYS

# client 1 - ed25519
$OPENSSL genpkey -algorithm ed25519 -out client/client.1.ed25519.key
$OPENSSL pkey -in client/client.1.ed25519.key -pubout -out client/client.1.ed25519.public.key
cat > client/cert.conf <<EOF
[req]
distinguished_name = req_distinguished_name
prompt = no

[req_distinguished_name]
commonName=my-device-hostname.com
organizationName=My Organization
organizationalUnitName=My Unit
emailAddress=myusername@example.com
countryName=NO
localityName=Oslo
stateOrProvinceName=Oslo
EOF
$OPENSSL req -new -key client/client.1.ed25519.key -out client/client.1.ed25519.req -config client/cert.conf
$OPENSSL x509 -req -CA tenant-ca/tenant.ca.crt -CAkey tenant-ca/tenant.ca.key -CAcreateserial -in client/client.1.ed25519.req -out client/client.1.ed25519.crt -days $DAYS

# client 1 - rsa
$OPENSSL genrsa -out client/client.1.rsa.key 3072
$OPENSSL rsa -in client/client.1.rsa.key -pubout -out client/client.1.rsa.public.key
cat > client/cert.conf <<EOF
[req]
distinguished_name = req_distinguished_name
prompt = no

[req_distinguished_name]
commonName=my-device-hostname.com
organizationName=My Organization
organizationalUnitName=My Unit
emailAddress=myusername@example.com
countryName=NO
localityName=Oslo
stateOrProvinceName=Oslo
EOF
$OPENSSL req -new -key client/client.1.rsa.key -out client/client.1.rsa.req -config client/cert.conf
$OPENSSL x509 -req -CA tenant-ca/tenant.ca.crt -CAkey tenant-ca/tenant.ca.key -CAcreateserial -in client/client.1.rsa.req -out client/client.1.rsa.crt -days $DAYS

# client 2 - rsa
$OPENSSL genrsa -out client/client.2.rsa.key 3072
$OPENSSL rsa -in client/client.2.rsa.key -pubout -out client/client.2.rsa.public.key
cat > client/cert.conf <<EOF
[req]
distinguished_name = req_distinguished_name
prompt = no

[req_distinguished_name]
commonName=my-device-hostname.com
organizationName=My Organization
organizationalUnitName=My Unit
emailAddress=myusername@example.com
countryName=NO
localityName=Oslo
stateOrProvinceName=Oslo
EOF
$OPENSSL req -new -key client/client.2.rsa.key -out client/client.2.rsa.req -config client/cert.conf
$OPENSSL x509 -req -CA tenant-ca/tenant.ca.crt -CAkey tenant-ca/tenant.ca.key -CAcreateserial -in client/client.2.rsa.req -out client/client.2.rsa.crt -days $DAYS
