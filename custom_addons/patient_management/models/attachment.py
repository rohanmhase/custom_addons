from odoo import models, fields, api
import boto3
from odoo.exceptions import UserError
import base64
import uuid
from datetime import datetime, timedelta


class PatientAttachment(models.Model):
    _name = "patient.attachment"
    _description = "Patient Attachments (X-Ray, MRI, Consent, Blood Report)"

    name = fields.Char(string="File Name", required=True)
    patient_id = fields.Many2one("clinic.patient", string="Patient", required=True, readonly=True)
    file_type = fields.Selection([
        ('consent', 'Consent Form'),
        ('xray', 'X-Ray'),
        ('mri', 'MRI'),
        ('blood_report', 'Blood Report'),
        ('other', 'Other')
    ], string="Type of Report", required=True)
    s3_url = fields.Char(string="S3 URL", readonly=True)
    file_data = fields.Binary(string="Upload File")
    admin = fields.Many2one("res.users", string="Admin/BM", required=True, default=lambda self: self.env.user, readonly=True)
    attachment_date = fields.Date(string="Attachment Date", required=True, readonly=True,
                                  default=lambda self: self._ist_date())
    other_description = fields.Char(string="Please specify (if Other)")

    preview_url = fields.Char(string="Preview URL", compute="_compute_preview_url")

    preview_html = fields.Html(
        string="Preview",
        compute="_compute_preview_html",
        sanitize=False
    )

    x_ray_id = fields.Many2one('patient.xray', string="Related X-Ray Record", readonly=True)

    x_ray_day = fields.Selection([("5", "5th Day"),
                                  ("20", "20th Day"),
                                  ("40", "40th Day"),
                                  ("60", "60th Day"),
                                  ("80", "80th Day")],
                                 string="Day of X-Ray", store=True)

    x_ray_actual_date = fields.Date(string="Date", store=True)

    x_ray_grade = fields.Selection([
        ('grade_0', 'Grade 0 - Normal'),
        ('grade_1', 'Grade 1 - Doubtful Joint space narrowing and possible osteophytic lipping.'),
        ('grade_2', 'Grade 2 - Definite osteophytes and possible joint space narrowing.'),
        ('grade_3', 'Grade 3 - Moderate multiple osteophytes, definite narrowing of joint space and some sclerosis and possible deformity of bone ends.'),
        ('grade_4', 'Grade 4 - Large osteophytes, marked narrowing of joint space, severe sclerosis and definite deformity of bone ends.'),
    ], string="Grade", store=True)

    x_ray_status = fields.Selection([("significant_positive", "Significant Positive"),
                                     ("mild_positive", "Mild Positive"),
                                     ("no_change", "No Change"),
                                     ("negative", "Negative"),
                                     ("baseline", "Baseline"),], string="X-Ray Status", required=True)

    active = fields.Boolean(default=True)

    @api.model_create_multi
    def create(self, vals_list):
        # Create attachment records
        records = super(PatientAttachment, self).create(vals_list)

        # Create X-Ray records for x-ray attachments
        for record in records:
            if record.file_type == 'xray':
                xray_data = {
                    'patient_id': record.patient_id.id,
                    'doctor_id': record.admin.id,
                    'x_ray_day': record.x_ray_day,
                    'x_ray_actual_date': record.x_ray_actual_date,
                    'x_ray_status': record.x_ray_status,
                    'grade': record.x_ray_grade,
                }

                print(xray_data)

                # Create X-Ray record and link it
                xray_record = self.env['patient.xray'].create(xray_data)
                record.x_ray_id = xray_record.id

        return records

    def write(self, vals):
        # Call super first
        result = super(PatientAttachment, self).write(vals)

        # Handle X-Ray updates
        for record in self:
            if record.file_type == 'xray' and record.x_ray_id:
                # Prepare X-Ray data
                xray_data = {}
                if 'x_ray_day' in vals:
                    xray_data['x_ray_day'] = vals['x_ray_day']
                if 'x_ray_actual_date' in vals:
                    xray_data['x_ray_actual_date'] = vals['x_ray_actual_date']
                if 'x_ray_grade' in vals:
                    xray_data['grade'] = vals['x_ray_grade']
                if 'x_ray_status' in vals:
                    xray_data['x_ray_status'] = vals['x_ray_status']

                if xray_data:
                    # Update the linked X-Ray record directly
                    record.x_ray_id.write(xray_data)

        return result

    @api.model
    def _get_s3_client(self):
        """Initialize S3 client from config or environment safely"""
        icp = self.env['ir.config_parameter'].sudo()
        region = icp.get_param('aws_s3_region_name') or 'ap-south-1'
        access_key = icp.get_param('aws_access_key_id')
        secret_key = icp.get_param('aws_secret_access_key')

        if not access_key or not secret_key:
            raise UserError("AWS access key or secret key not configured!")

        return boto3.client(
            's3',
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region
        )

    def action_upload_to_s3(self):
        """Uploads the file to AWS S3 and stores the URL"""
        for rec in self:
            if not rec.file_data:
                raise UserError("Please upload a file first.")

            s3 = rec._get_s3_client()
            bucket = self.env['ir.config_parameter'].sudo().get_param('aws_s3_bucket_name')
            if not bucket:
                raise UserError("AWS S3 bucket name not configured!")

            # Generate a unique file name
            file_name = f"{uuid.uuid4()}_{rec.name}"
            file_content = base64.b64decode(rec.file_data)

            # Detect content type
            content_type = "application/octet-stream"
            content_disp = "inline"
            lower_name = rec.name.lower()

            if lower_name.endswith(".pdf"):
                content_type = "application/pdf"
            elif lower_name.endswith(".jpg") or lower_name.endswith(".jpeg"):
                content_type = "image/jpeg"
            elif lower_name.endswith(".png"):
                content_type = "image/png"
            elif lower_name.endswith(".txt"):
                content_type = "text/plain"

            # Upload file to S3 with correct headers
            s3.put_object(
                Bucket=bucket,
                Key=file_name,
                Body=file_content,
                ACL='private',  # recommended for sensitive files
                ContentType=content_type,
                ContentDisposition=content_disp
            )

            # Generate URL (you may use presigned URL for restricted access)
            region = s3.meta.region_name
            rec.s3_url = f"https://{bucket}.s3.{region}.amazonaws.com/{file_name}"

            # Clear local file data
            rec.file_data = False

    @api.onchange('file_type')
    def _onchange_file_type(self):
        if self.file_type != 'other':
            self.other_description = False  # clear if not "Other"

    @api.constrains('file_type', 'other_description')
    def _check_other_description(self):
        for rec in self:
            if rec.file_type == 'other' and not rec.other_description:
                raise UserError("Please specify the description for 'Other' file type.")

    def _compute_preview_url(self):
        for rec in self:
            rec.preview_url = False
            if rec.s3_url:
                s3 = rec._get_s3_client()
                bucket = self.env['ir.config_parameter'].sudo().get_param('aws_s3_bucket_name')

                # Extract S3 key from URL
                key = rec.s3_url.split('/')[-1]

                rec.preview_url = s3.generate_presigned_url(
                    ClientMethod='get_object',
                    Params={
                        'Bucket': bucket,
                        'Key': key
                    },
                    ExpiresIn=300  # 5 minutes
                )

    def _compute_preview_html(self):
        for rec in self:
            rec.preview_html = False
            if rec.preview_url:
                rec.preview_html = f"""
                    <iframe src="{rec.preview_url}"
                            width="100%"
                            height="600px"
                            style="border:1px solid #ddd;"
                            allowfullscreen>
                    </iframe>
                """

    def _ist_date(self):
        utc = (datetime.now())
        td = timedelta(hours=5, minutes=30)
        ist_date = utc + td
        return ist_date.date()

    def unlink(self):
        for record in self:
            record.active = False
        # Do not call super() → prevents actual deletion
        return True