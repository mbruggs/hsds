#!/bin/bash
#
# create zip file for deployment to AWS Lambda
#

ZIPFILE="function.zip"
if [ -f ${ZIPFILE} ]; then
   rm ${ZIPFILE}
fi

# copy util files that are the same for lambda
SRC=../hsds/util
DES=chunkread/hsds/util
cp $SRC/arrayUtil.py $DES
cp $SRC/hdf5dtype.py $DES
cp $SRC/domainUtil.py $DES

zip ${ZIPFILE} chunkread/lambda_function.py
zip ${ZIPFILE} chunkread/__init__.py
zip ${ZIPFILE} chunkread/hsds/*.py
zip ${ZIPFILE} chunkread/hsds/util/*.py

pip install --target ./package numpy
#pip install --target ./package aiobotocore
#pip install --target ./package aiohttp
#pip install --target ./package numba

cd package
zip -r9 ${OLDPWD}/function.zip .

cd -
