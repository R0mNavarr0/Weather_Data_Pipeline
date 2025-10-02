########################################
# 1) SSH Key
########################################
resource "aws_key_pair" "terraform_key" {
  key_name   = var.key_name
  public_key = file(var.public_key_path)
}

########################################
# 2) Security Group
########################################
resource "aws_security_group" "ec2_sg" {
  name        = "ec2_sg"
  description = "Allow SSH and MongoDB"

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  # Recommande: restreindre 27017 (ex: ton IP publique)
  ingress {
    from_port   = 27017
    to_port     = 27017
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

########################################
# 3) IAM (EC2 -> ECR + S3 + CloudWatch)
########################################
resource "aws_iam_role" "ec2_role" {
  name = "ec2-ecr-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Effect = "Allow",
      Principal = { Service = "ec2.amazonaws.com" },
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecr_access" {
  role       = aws_iam_role.ec2_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

# S3 backups write
resource "aws_iam_policy" "s3_backups_write" {
  name   = "s3-backups-write"
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      { Effect = "Allow", Action = ["s3:PutObject","s3:AbortMultipartUpload"], Resource = "${aws_s3_bucket.mongo_backups.arn}/*" },
      { Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.mongo_backups.arn }
    ]
  })
}
resource "aws_iam_role_policy_attachment" "s3_backups_write_attach" {
  role       = aws_iam_role.ec2_role.name
  policy_arn = aws_iam_policy.s3_backups_write.arn
}

# CloudWatch Agent
resource "aws_iam_role_policy_attachment" "cwagent_attach" {
  role       = aws_iam_role.ec2_role.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
}

resource "aws_iam_instance_profile" "ec2_profile" {
  name = "ec2-instance-profile"
  role = aws_iam_role.ec2_role.name
}

########################################
# 4) S3 bucket backups (v5 resources)
########################################
resource "aws_s3_bucket" "mongo_backups" {
  bucket = var.backup_bucket_name
  tags   = { Name = "mongo-backups" }
}

resource "aws_s3_bucket_versioning" "mongo_backups" {
  bucket = aws_s3_bucket.mongo_backups.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_lifecycle_configuration" "mongo_backups" {
  bucket = aws_s3_bucket.mongo_backups.id
  rule {
    id     = "expire30d"
    status = "Enabled"
    filter {prefix = ""}
    expiration { days = 30 }
    noncurrent_version_expiration { noncurrent_days = 30 }
  }
}

########################################
# 5) ECR repo
########################################
resource "aws_ecr_repository" "etl_repo" {
  name                 = "greenandcoop-etl"
  image_tag_mutability = "MUTABLE"
  image_scanning_configuration { scan_on_push = true }
  tags = { Name = "greenandcoop-etl-repo" }
}

# Build+push local après création du repo
resource "null_resource" "push_etl_image" {
  depends_on = [aws_ecr_repository.etl_repo]
  provisioner "local-exec" {
    working_dir = "../"
    command = <<EOT
      ACCOUNT_ID=${var.account_id}
      REGION=${var.aws_region}
      REPO=${aws_ecr_repository.etl_repo.name}

      echo "==> Build etl"
      docker compose build etl

      echo "==> Tag etl"
      docker tag etl:latest $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/$REPO:latest

      echo "==> ECR login"
      aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com

      echo "==> Push etl"
      docker push $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/$REPO:latest
    EOT
  }
}

########################################
# 6) EC2 + user_data (réseau, ETL, backup, CloudWatch)
########################################
resource "aws_instance" "app_ec2" {
  ami                  = var.ami_id
  instance_type        = var.instance_type
  key_name             = aws_key_pair.terraform_key.key_name
  security_groups      = [aws_security_group.ec2_sg.name]
  iam_instance_profile = aws_iam_instance_profile.ec2_profile.name
  depends_on           = [null_resource.push_etl_image]

  user_data = <<-EOF
    #!/bin/bash
    set -e

    apt-get update -y
    apt-get install -y docker.io awscli curl gnupg cron
    systemctl enable --now docker
    systemctl enable --now cron

    docker network create app-net || true

    # Mongo
    docker run -d \
      --name mongo \
      --network app-net \
      -p 27017:27017 \
      -e MONGO_INITDB_ROOT_USERNAME=${var.mongo_root_username} \
      -e MONGO_INITDB_ROOT_PASSWORD=${var.mongo_root_password} \
      -v /data/mongo:/data/db \
      mongo:8.0

    # ETL (one-shot)
    aws ecr get-login-password --region ${var.aws_region} | docker login --username AWS --password-stdin ${var.account_id}.dkr.ecr.${var.aws_region}.amazonaws.com
    docker run -d \
      --name etl \
      --network app-net \
      -e MONGO_URI="mongodb://${var.mongo_root_username}:${var.mongo_root_password}@mongo:27017/greenandcoop?authSource=admin" \
      ${var.account_id}.dkr.ecr.${var.aws_region}.amazonaws.com/greenandcoop-etl:latest

    # Backup script (03:00 daily)
    cat >/usr/local/bin/mongo_backup.sh <<BKP
    #!/bin/bash
    set -e
    TS=\$(date +"%Y%m%d%H%M")
    TMPDIR=\$(mktemp -d)
    ARCHIVE="/tmp/mongo_\$TS.tar.gz"

    docker run --rm --network app-net -v "\$TMPDIR":/dump mongo:8.0 \
      bash -lc 'mongodump --uri="mongodb://${var.mongo_root_username}:${var.mongo_root_password}@mongo:27017/greenandcoop?authSource=admin" --out /dump'

    tar -czf "\$ARCHIVE" -C "\$TMPDIR" .
    aws s3 cp "\$ARCHIVE" s3://${var.backup_bucket_name}/backups/mongo_\$TS.tar.gz
    rm -rf "\$TMPDIR" "\$ARCHIVE"
    echo "\$(date -Iseconds) backup OK: mongo_\$TS.tar.gz"
    BKP
    chmod +x /usr/local/bin/mongo_backup.sh

    cat >/etc/cron.d/mongo_backup <<CRN
    SHELL=/bin/bash
    PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
    0 3 * * * root /usr/local/bin/mongo_backup.sh >> /var/log/mongo_backup.log 2>&1
    CRN
    chmod 0644 /etc/cron.d/mongo_backup
    touch /var/log/mongo_backup.log
    systemctl restart cron || true

    # CloudWatch Agent
    CW_PKG=/tmp/amazon-cloudwatch-agent.deb
    curl -fsSL -o \$CW_PKG https://s3.amazonaws.com/amazoncloudwatch-agent/ubuntu/amd64/latest/amazon-cloudwatch-agent.deb
    dpkg -i -E \$CW_PKG

    cat >/opt/aws/amazon-cloudwatch-agent/bin/config.json <<'CFG'
    {
      "metrics": {
        "append_dimensions": {
          "AutoScalingGroupName": "$${aws:AutoScalingGroupName}",
          "ImageId": "$${aws:ImageId}",
          "InstanceId": "$${aws:InstanceId}",
          "InstanceType": "$${aws:InstanceType}"
        },
        "aggregation_dimensions": [["InstanceId"]],
        "metrics_collected": {
          "cpu":  { "measurement": ["cpu_usage_active"], "metrics_collection_interval": 60 },
          "mem":  { "measurement": ["mem_used_percent"], "metrics_collection_interval": 60 },
          "disk": { "measurement": ["used_percent"], "resources": ["*"], "metrics_collection_interval": 60 }
        }
      },
      "logs": {
        "logs_collected": {
          "files": {
            "collect_list": [
              { "file_path": "/var/log/mongo_backup.log", "log_group_name": "mongo-backup", "log_stream_name": "{instance_id}" },
              { "file_path": "/var/log/cloud-init-output.log", "log_group_name": "cloud-init", "log_stream_name": "{instance_id}" }
            ]
          }
        }
      }
    }
    CFG

    /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
      -a fetch-config -m ec2 -c file:/opt/aws/amazon-cloudwatch-agent/bin/config.json -s
  EOF

  tags = { Name = "greenandcoop-ec2" }
}