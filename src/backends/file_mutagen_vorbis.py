# SPDX-License-Identifier: MIT
# (c) 2023 knuxify and Ear Tag contributors

from gi.repository import GObject
import base64
import tempfile
import magic
import mimetypes
from PIL import Image

from mutagen.flac import FLAC, Picture, error as FLACError
from mutagen.id3 import PictureType

from .file_mutagen_common import EartagFileMutagenCommon

class EartagFileMutagenVorbis(EartagFileMutagenCommon):
    """EartagFile handler that uses mutagen for Voris Comment support."""
    __gtype_name__ = 'EartagFileMutagenVorbis'
    _supports_album_covers = True
    _supports_full_dates = True

    # There's an official standard and semi-official considerations for tags,
    # plus some more documents linked from https://wiki.xiph.org/VorbisComment;
    # this only covers tags mentioned there.
    supported_extra_tags = (
        'composer', 'copyright', 'encodedby', 'mood', 'discnumber', 'publisher',
        'isrc',

        'albumartistsort', 'albumsort', 'composersort', 'artistsort', 'titlesort'
    )

    _replaces = {
        'releasedate': 'date',
        # There's also ENCODED-BY, but confusingly it represents... the person doing the encoding?
        'encodedby': 'encoder'
    }

    def load_from_file(self, path):
        super().load_from_file(path)
        self._cover_path = None
        self.coverart_tempfile = None
        self.load_cover()
        self.setup_present_extra_tags()
        self.setup_original_values()

    def get_tag(self, tag_name):
        """Tries the lowercase, then uppercase representation of the tag."""
        if tag_name.lower() in self._replaces:
            tag_name = self._replaces[tag_name.lower()]
        try:
            if tag_name in self.int_properties:
                return self.mg_file.tags[tag_name.lower()][0]
            else:
                return self.mg_file.tags[tag_name.lower()][0] or ''
        except KeyError:
            try:
                if tag_name in self.int_properties:
                    return self.mg_file.tags[tag_name.upper()][0]
                else:
                    return self.mg_file.tags[tag_name.upper()][0] or ''
            except KeyError:
                if tag_name in self.int_properties:
                    return None
                else:
                    return ''

    def set_tag(self, tag_name, value):
        """Sets the tag with the given name to the given value."""
        if tag_name.lower() in self._replaces:
            tag_name = self._replaces[tag_name.lower()]
        if tag_name.upper() in self.mg_file.tags:
            self.mg_file.tags[tag_name.upper()] = str(value)
        else:
            self.mg_file.tags[tag_name] = str(value)

    def has_tag(self, tag_name):
        """
        Returns True or False based on whether the tag with the given name is
        present in the file.
        """
        if tag_name == 'totaltracknumber':
            return bool(self.totaltracknumber)
        elif tag_name == 'encodedby':
            if 'encoder' in self.mg_file.tags:
                return bool(self.mg_file.tags['encoder'][0])
            elif 'ENCODER' in self.mg_file.tags:
                return bool(self.mg_file.tags['ENCODER'][0])
            else:
                return False
        if tag_name.lower() in self._replaces:
            tag_name = self._replaces[tag_name.lower()]
        if tag_name in self.mg_file.tags or tag_name.upper() in self.mg_file.tags:
            return True
        return False

    def delete_tag(self, tag_name):
        """Deletes the tag with the given name from the file."""
        _original_tag_name = tag_name
        if tag_name.lower() == 'releasedate':
            self._releasedate_cached = ''
        if tag_name.lower() in self._replaces:
            tag_name = self._replaces[tag_name.lower()]
        if tag_name in self.mg_file.tags:
            del self.mg_file.tags[tag_name]
        self.mark_as_modified(_original_tag_name)

    def on_remove(self, *args):
        if self.coverart_tempfile:
            self.coverart_tempfile.close()
        super().on_remove()

    @GObject.Property(type=str)
    def cover_path(self):
        return self._cover_path

    @cover_path.setter
    def cover_path(self, value):
        self._cover_path = value

        with open(value, "rb") as cover_file:
            data = cover_file.read()

        # shamelessly stolen from
        # https://stackoverflow.com/questions/1996577/how-can-i-get-the-depth-of-a-jpg-file
        mode_to_bpp = {"1": 1, "L": 8, "P": 8, "RGB": 24, "RGBA": 32, "CMYK": 32,
            "YCbCr": 24, "LAB": 24, "HSV": 24, "I": 32, "F": 32}

        picture = Picture()
        picture.data = data
        picture.type = PictureType.COVER_FRONT
        picture.mime = magic.from_file(value, mime=True)
        img = Image.open(value)
        picture.width = img.width
        picture.height = img.height
        picture.depth = mode_to_bpp[img.mode]

        if isinstance(self.mg_file, FLAC):
            picture.type = PictureType.COVER_FRONT
            for _pic in self.mg_file.pictures:
                if _pic.type in (PictureType.COVER_FRONT, PictureType.OTHER):
                    self.mg_file.pictures.remove(_pic)
            self.mg_file.add_picture(picture)
        else:
            picture_data = picture.write()
            encoded_data = base64.b64encode(picture_data)
            vcomment_value = encoded_data.decode("ascii")

            self.mg_file["metadata_block_picture"] = [vcomment_value]

        self.mark_as_modified('cover_path')

    def load_cover(self):
        """Loads cover data from file."""
        # See https://mutagen.readthedocs.io/en/latest/user/vcomment.html.
        # There are three ways to get the cover image:

        # 1. Using `mutagen.flac.FLAC.pictures`
        if isinstance(self.mg_file, FLAC) and self.mg_file.pictures:
            picture_cover = None
            picture_other = None

            # We run this in two loops so that we can prio
            for picture in self.mg_file.pictures:
                if picture.type == PictureType.COVER_FRONT:
                    picture_cover = picture

                elif picture.type == PictureType.OTHER:
                    picture_other = picture
                    return

            if picture_cover:
                picture = picture_cover
            elif picture_other:
                picture = picture_other
            else:
                self.notify('cover_path')
                return

            cover_extension = mimetypes.guess_extension(picture.mime)
            self.coverart_tempfile = tempfile.NamedTemporaryFile(
                suffix=cover_extension
            )
            self.coverart_tempfile.write(picture.data)
            self.coverart_tempfile.flush()
            self._cover_path = self.coverart_tempfile.name

        # 2. Using metadata_block_picture
        elif self.mg_file.get("metadata_block_picture", []):
            for b64_data in self.mg_file.get("metadata_block_picture", []):
                try:
                    data = base64.b64decode(b64_data)
                except (TypeError, ValueError):
                    continue

                try:
                    cover_picture = Picture(data)
                except FLACError:
                    continue

                cover_extension = mimetypes.guess_extension(cover_picture.mime)

                self.coverart_tempfile = tempfile.NamedTemporaryFile(
                    suffix=cover_extension
                )
                self.coverart_tempfile.write(cover_picture.data)
                self.coverart_tempfile.flush()
                self._cover_path = self.coverart_tempfile.name

        # 3. Using the coverart field (and optionally covermime)
        else:
            covers = self.mg_file.get("coverart", [])
            mimes = self.mg_file.get("coverartmime", [])

            n = 0
            for cover in covers:
                try:
                    data = base64.b64decode(cover.encode("ascii"))
                except (TypeError, ValueError):
                    continue

                if not data:
                    continue

                cover_extension = mimetypes.guess_extension(
                    magic.from_buffer(data, mime=True)
                )
                if not cover_extension and mimes and len(mimes) == len(covers):
                    cover_extension = mimes[n]

                self.coverart_tempfile = tempfile.NamedTemporaryFile(
                    suffix=cover_extension
                )
                self.coverart_tempfile.write(data)
                self.coverart_tempfile.flush()
                self._cover_path = self.coverart_tempfile.name
                n += 1

        self.notify('cover_path')

    @GObject.Property(type=int)
    def tracknumber(self):
        tracknum_raw = self.get_tag('tracknumber')
        if not tracknum_raw:
            return None
        if '/' in tracknum_raw:
            return int(tracknum_raw.split('/')[0])
        return int(tracknum_raw)

    @tracknumber.setter
    def tracknumber(self, value):
        if self.totaltracknumber:
            self.set_tag('tracknumber', '{n}/{t}'.format(
                n=str(value), t=str(self.totaltracknumber))
            )
        else:
            if value:
                self.set_tag('tracknumber', str(value))
            elif self.has_tag('tracknumber'):
                self.delete_tag('tracknumber')
        self.mark_as_modified('tracknumber')

    @GObject.Property(type=int)
    def totaltracknumber(self):
        tracknum_raw = self.get_tag('tracknumber')
        if not tracknum_raw:
            return None
        if '/' in tracknum_raw:
            return int(tracknum_raw.split('/')[1])
        return None

    @totaltracknumber.setter
    def totaltracknumber(self, value):
        if self.tracknumber:
            self.set_tag('tracknumber', '{n}/{t}'.format(
                n=str(self.tracknumber), t=str(value))
            )
        else:
            if value:
                self.set_tag('tracknumber', '0/{t}'.format(t=str(value)))
            elif self.has_tag('tracknumber'):
                self.delete_tag('tracknumber')
        self.mark_as_modified('totaltracknumber')

    @GObject.Property(type=int)
    def discnumber(self):
        discnum_raw = self.get_tag('discnumber')
        if not discnum_raw:
            return None
        if '/' in discnum_raw:
            return int(discnum_raw.split('/')[0])
        return int(discnum_raw)

    @discnumber.setter
    def discnumber(self, value):
        if value:
            self.set_tag('discnumber', str(value))
        elif self.has_tag('discnumber'):
            self.delete_tag('discnumber')
        self.mark_as_modified('discnumber')
