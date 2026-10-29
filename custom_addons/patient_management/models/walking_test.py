from odoo import fields, models, api
from datetime import datetime, timedelta
import boto3
import base64
import uuid
from odoo.exceptions import UserError, ValidationError
from urllib.parse import unquote_plus


class WalkingTest(models.Model):
    _name = "patient.walking_test"
    _description = "Walking Test"

    patient_id = fields.Many2one("clinic.patient", string="Patient", required=True, readonly=True)
    walking_test_date = fields.Date(string="Date", default=lambda self: self._ist_date(), readonly=True, required=True)
    doctor_id = fields.Many2one("res.users", string="Doctor", required=True, readonly=True,
                                default=lambda self: self.env.user)
    distance = fields.Integer(string="Distance", required=True)
    time_min = fields.Selection(
        [(str(i), str(i)) for i in range(0, 60)],
        string="Minutes", required=True,
    )
    time_sec = fields.Selection(
        [(str(i), str(i)) for i in range(0, 60)],
        string="Seconds", required=True,
    )
    right_knee_pain = fields.Integer(string="Right Knee Pain %", required=True, default=False)
    left_knee_pain = fields.Integer(string="Left Knee Pain %", required=True, default=False)
    spine_pain = fields.Integer(string="Spine Pain %", required=True, default=False)
    limping = fields.Selection([('observed', 'Observed'),
                                ('not_observed', 'Not Observed')],
                               string="Limping", required=True)
    step_symmetry = fields.Selection([('both_steps_appear_equal', 'Both Steps Appear Equal'),
                                      ('step_appear_unequal', 'Step Appear Unequal')],
                                     string="Step Symmetry", required=True)
    foot_dragging = fields.Selection([('observed', 'Observed'),
                                      ('not_observed', 'Not Observed')],
                                     string="Foot Dragging", required=True)
    walking_aid_used = fields.Selection([('none', 'None'),
                                         ('stick', 'Stick'),
                                         ('crutches', 'Crutches'),
                                         ('walker', 'Walker'),
                                         ('other', 'Other')],
                                        string="Walking Aid Used", required=True)
    other_wau = fields.Char(string="Specify if Other")
    add_observation = fields.Char(string="Additional Observation")
    active = fields.Boolean(default=True)
    video_data = fields.Binary(string="Upload Video")
    video_name = fields.Char(string="Video File Name", copy=False)
    s3_video_url = fields.Char(string="S3 Video URL", readonly=True)

    preview_url = fields.Char(string="Preview URL", compute="_compute_preview_url")
    preview_html = fields.Html(
        string="Video Preview",
        compute="_compute_preview_html",
        sanitize=False
    )

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

    @api.model
    def _get_s3_client(self):
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

    def action_upload_video_to_s3(self):
        """Uploads the video file to AWS S3 and stores the URL"""
        for rec in self:
            if not rec.video_data:
                raise UserError("Please upload a video file first.")
            if not rec.video_name:
                raise UserError("Please provide a file name for the video.")

            s3 = rec._get_s3_client()
            bucket = self.env['ir.config_parameter'].sudo().get_param('aws_s3_bucket_name')
            if not bucket:
                raise UserError("AWS S3 bucket name not configured!")

            # UPDATE HERE: Use the exact folder name with a trailing slash
            file_name = f"Walking Test Videos/{uuid.uuid4()}_{rec.video_name}"

            file_content = base64.b64decode(rec.video_data)

            # Detect video content type
            content_type = "application/octet-stream"
            lower_name = rec.video_name.lower()

            if lower_name.endswith(".mp4"):
                content_type = "video/mp4"
            elif lower_name.endswith(".mov"):
                content_type = "video/quicktime"
            elif lower_name.endswith(".avi"):
                content_type = "video/x-msvideo"
            elif lower_name.endswith(".webm"):
                content_type = "video/webm"

            # Upload to S3
            s3.put_object(
                Bucket=bucket,
                Key=file_name,
                Body=file_content,
                ACL='private',
                ContentType=content_type,
                ContentDisposition="inline"
            )

            # Generate S3 URL
            region = s3.meta.region_name
            # S3 URLs automatically encode spaces as '+' or '%20'. For safety in raw strings, use the encoded key or rely on standard formatting.
            rec.s3_video_url = f"https://{bucket}.s3.{region}.amazonaws.com/{file_name.replace(' ', '+')}"

            # Clear local binary data to save database space
            rec.video_data = False

    def _compute_preview_url(self):
        for rec in self:
            rec.preview_url = False
            if rec.s3_video_url:
                s3 = rec._get_s3_client()
                bucket = self.env['ir.config_parameter'].sudo().get_param('aws_s3_bucket_name')

                # Extract the raw key from the URL
                raw_key = '/'.join(rec.s3_video_url.split('/')[3:])

                # Decode the key to convert '+' and '%20' back to real spaces
                key = unquote_plus(raw_key)

                rec.preview_url = s3.generate_presigned_url(
                    ClientMethod='get_object',
                    Params={'Bucket': bucket, 'Key': key},
                    ExpiresIn=300
                )

    def _compute_preview_html(self):
        for rec in self:
            rec.preview_html = False
            if rec.preview_url:
                rec.preview_html = f"""
                    <div style="width: 100%; text-align: center; margin-top: 15px;">
                        <video style="max-width: 100%; height: auto; max-height: 500px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1);" controls controlsList="nodownload">
                            <source src="{rec.preview_url}" type="video/mp4">
                            Your browser does not support the video tag.
                        </video>
                    </div>
                """

    @api.constrains('right_knee_pain', 'left_knee_pain', 'spine_pain')
    def _check_pain_percentages(self):
        for rec in self:
            if rec.right_knee_pain < 0 or rec.right_knee_pain > 100:
                raise ValidationError("Right Knee Pain must be between 0 and 100.")
            if rec.left_knee_pain < 0 or rec.left_knee_pain > 100:
                raise ValidationError("Left Knee Pain must be between 0 and 100.")
            if rec.spine_pain < 0 or rec.spine_pain > 100:
                raise ValidationError("Spine Pain must be between 0 and 100.")