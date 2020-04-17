import json
import base64
from botocore.exceptions import ClientError


from .hsds.chunkread import read_hyperslab, read_points, get_app
from .hsds import hsds_logger as log


def lambda_handler(event, context):

    # run hyperslab or point selection based on event values
    app = get_app()
    params = {}
    b64data = None
    for k in ("chunk_id", "dset_json", "bucket", "s3path", "s3offset", "s3offset", "s3size", "num_points"):
        if k in event:
            log.debug(f"setting parameter: {k} to: {event[k]}")
            params[k] = event[k]
    if "select" in event:
        # selection
        status_code = 500
        select_str = event["select"]
        if select_str[0] == '[' and select_str[-1] == ']':
            select_str = select_str[1:-1]
        fields = select_str.split(',')
        slices = []
        for extent in fields:
            extent_fields = extent.split(':')
            start = int(extent_fields[0])
            stop = int(extent_fields[1])
            if len(extent_fields) > 2:
                step = int(extent_fields[2])
            else:
                step = 1
            slices.append(slice(start, stop, step))
        params["slices"] = slices
    # params["select"]= "[1:2,0:8:2]" -> ((slice(1,2,1),slice(0,8,2)))
    try:
        if "point_arr" in event:
            # point selection
            params["point_arr"] = event["point_arr"]
            b64data = read_points(app, params)
        else:
            # hyperslab selection
            b64data = read_hyperslab(app, params)
    except ClientError as ce:
        response_code = ce.response["Error"]["Code"]
        status_code = 500
        if response_code in ("NoSuchKey", "404") or response_code == 404:
            status_code = 404
        elif response_code == "NoSuchBucket":
            status_code = 404
        elif response_code in ("AccessDenied", "401", "403") or response_code in (401, 403):
            status_code = 403
        else:
            status_code = 500
    except KeyError:
        status_code = 500
    rsp = { 'statusCode': status_code }
    if b64data:
        rsp['body'] = b64data
    return rsp
