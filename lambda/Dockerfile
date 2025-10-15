FROM public.ecr.aws/lambda/python:3.11

# Install system dependencies for PIL
RUN yum update -y && \
    yum install -y \
    libjpeg-devel \
    zlib-devel \
    freetype-devel \
    lcms2-devel \
    libwebp-devel \
    tiff-devel \
    openjpeg2-devel \
    && yum clean all

# Copy requirements and install Python dependencies
COPY requirements.txt ${LAMBDA_TASK_ROOT}
RUN pip install -r requirements.txt

# Copy function code
COPY pii_handler.py ${LAMBDA_TASK_ROOT}

# Set the CMD to your handler
CMD ["pii_handler.lambda_handler"]
