#!/usr/bin/env python
# coding=utf-8
#
# Copyright (c) 2023 David Burghoff <burghoff@utexas.edu>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
#

# Some functions for getting the properties of fonts and characters.
# Three libraries are used:
#  fontconfig: Used for discovering fonts based on the SVG style. This uses
#              Inkscape's libfontconfig, so it should always match what Inkscape does
#  fonttools:  Gets font properties once discovered (from font's filename)
#  Pango:      Used to render test characters (included with GTK)
#              Sets up a blank GTK window and renders Pango text, reusing
#              the same layout for all rendering.

import inkex
import os, warnings, sys, re, ctypes


# The fontconfig library is used to select a font given its CSS specs
# This library should work starting with v1.0
# Due to the way fontconfig is structured, we have to patch
# ctypes's LoadLibrary to help it find libfontconfig
from inkex.text.utils import default_style_atts, Get_Binary_Loc
from inkex import Style

original_load_library = ctypes.cdll.LoadLibrary


def custom_load_library(name):
    if name in ["libfontconfig.so.1", "libfreetype.so.6"]:
        LIBNAME = {
            "linux": {
                "libfreetype.so.6": "libfreetype.so.6",
                "libfontconfig.so.1": "libfontconfig.so.1",
            },
            "openbsd6": {
                "libfreetype.so.6": "libfreetype.so.28",
                "libfontconfig.so.1": "libfontconfig.so.11",
            },
            "darwin": {
                "libfreetype.so.6": "libfreetype.6.dylib",
                "libfontconfig.so.1": "libfontconfig.1.dylib",
            },
            "win32": {
                "libfreetype.so.6": "libfreetype-6.dll",
                "libfontconfig.so.1": "libfontconfig-1.dll",
            },
        }[sys.platform][name]

        if "SI_FC_DIR" in os.environ:
            fpath = os.path.abspath(os.path.join(os.environ["SI_FC_DIR"], LIBNAME))
            ret = original_load_library(fpath)
        else:
            try:
                ret = original_load_library(LIBNAME)
            except FileNotFoundError:
                blocdir = os.path.dirname(Get_Binary_Loc())
                fpath = os.path.abspath(os.path.join(blocdir, LIBNAME))
                ret = original_load_library(fpath)
        return ret
    elif name == "libc.so.6":
        # Do not need to load, return a blank class consistent with fontconfig
        libc = type("libc", (object,), {"free": staticmethod(lambda ptr: None)})()
        libc.free.argtypes = (ctypes.c_void_p,)
        return libc
    else:
        return original_load_library(name)


from unittest.mock import patch

with patch("ctypes.cdll.LoadLibrary", side_effect=custom_load_library):
    try:
        import fontconfig as fc
    except ModuleNotFoundError:
        import inkex.text.packages.python_fontconfig.fontconfig as fc  # type: ignore
FC = fc.FC


class FontConfig:
    def __init__(self):
        self.truefonts = dict()
        # css
        self.truefontsfc = dict()
        # fontconfig
        self.truefontsft = dict()
        # fonttools
        self.fontcharsets = dict()

        self.disable_lcctype()

    # MacOS can throw a warning if LC_CTYPE not disabled
    def disable_lcctype(self):
        self.lcctype = os.environ.get("LC_CTYPE")
        if self.lcctype is not None and sys.platform == "darwin":
            del os.environ["LC_CTYPE"]  # suppress Mac warning

    # Not actually needed since env vars won't persist
    def enable_lcctype(self):
        if self.lcctype is not None and sys.platform == "darwin":
            os.environ["LC_CTYPE"] = self.lcctype

    # Use fontconfig to get the true font that most text will be rendered as
    def get_true_font(self, reducedsty):
        nftuple = tuple(reducedsty.items())  # for hashing
        if nftuple not in self.truefonts:
            pat = self.css_to_fc_pattern(reducedsty)
            conf = fc.Config.get_current()
            conf.substitute(pat, FC.MatchPattern)
            pat.default_substitute()
            found, status = conf.font_match(pat)
            truefont = self.fcfound_to_css(found)

            self.truefonts[nftuple] = truefont
            self.truefontsfc[nftuple] = found
            self.fontcharsets[tuple(truefont.items())] = found.get(fc.PROP.CHARSET, 0)[
                0
            ]
        return self.truefonts[nftuple]

    # Sometimes, a font will not have every character and a different one is
    # substituted. (For example, many fonts do not have the ⎣ character.)
    # Gets the true font by character
    def get_true_font_by_char(self, reducedsty, chars):
        nftuple = tuple(reducedsty.items())
        if nftuple in self.truefonts:
            truefont = self.truefonts[nftuple]
            d = {
                k: truefont
                for k in chars
                if ord(k) in self.fontcharsets[tuple(truefont.items())]
            }
        else:
            d = {}

        if len(d) < len(chars):
            pat = self.css_to_fc_pattern(reducedsty)
            conf = fc.Config.get_current()
            conf.substitute(pat, FC.MatchPattern)
            pat.default_substitute()

            found, total_coverage, status = conf.font_sort(
                pat, trim=True, want_coverage=False
            )
            for f in found:
                truefont = self.fcfound_to_css(f)
                cs = f.get(fc.PROP.CHARSET, 0)[0]
                self.fontcharsets[tuple(truefont.items())] = cs
                d2 = {k: truefont for k in chars if ord(k) in cs and k not in d}
                d.update(d2)
                if len(d) == len(chars):
                    break
            if len(d) < len(chars):
                # dh.idebug('Not found in any font: '+str([str(ord(c)) for c in chars if c not in d]))
                # foundcs = sorted(list(set([cf for k,v in self.fontcharsets.items() for cf in v])))
                # dh.idebug('Found: '+str([str(c) for c in foundcs]))
                d.update({c: None for c in chars if c not in d})
        return d

    def get_fonttools_font(self, reducedsty):
        nftuple = tuple(reducedsty.items())  # for hashing
        if nftuple not in self.truefontsft:
            if nftuple not in self.truefontsfc:
                self.get_true_font(reducedsty)
            found = self.truefontsfc[nftuple]
            self.truefontsft[nftuple] = FontTools_FontInstance(found)
        return self.truefontsft[nftuple]

    # Convert a style dictionary to an fc search pattern
    def css_to_fc_pattern(self, sty):
        pat = fc.Pattern.name_parse(
            re.escape(sty["font-family"].replace("'", "").replace('"', ""))
        )
        pat.add(
            fc.PROP.WIDTH,
            lu.CSSSTR_to_FCWDT.get(sty.get("font-stretch"), FC.WIDTH_NORMAL),
        )
        pat.add(
            fc.PROP.WEIGHT,
            lu.CSSWGT_to_FCWGT.get(sty.get("font-weight"), FC.WEIGHT_NORMAL),
        )
        pat.add(
            fc.PROP.SLANT, lu.CSSSTY_to_FCSLN.get(sty.get("font-style"), FC.SLANT_ROMAN)
        )
        return pat

    # Convert a found fc object to a Style
    def fcfound_to_css(self, f):
        # For CSS, enclose font family in single quotes
        # Needed for fonts like Modern No. 20 with periods in the family
        fcfam = f.get(fc.PROP.FAMILY, 0)[0]
        fcwgt = f.get(fc.PROP.WEIGHT, 0)[0]
        fcsln = f.get(fc.PROP.SLANT, 0)[0]
        fcwdt = f.get(fc.PROP.WIDTH, 0)[0]
        if any([isinstance(v, tuple) for v in [fcfam, fcwgt, fcsln, fcwdt]]):
            return None
        else:
            return Style(
                [
                    ("font-family", "'" + fcfam.strip("'") + "'"),
                    ("font-weight", nearest_val(lu.FCWGT_to_CSSWGT, fcwgt)),
                    ("font-style", lu.FCSLN_to_CSSSTY[fcsln]),
                    ("font-stretch", nearest_val(lu.FCWDT_to_CSSSTR, fcwdt)),
                ]
            )

    # For testing purposes
    def Flow_Test_Doc(self):
        # dh.tic()
        SIZE = 10
        # selected_families = ['Arial']
        selected_families = None

        conf = fc.Config.get_current()
        pat = fc.Pattern.create(vals=())
        conf.substitute(pat, FC.MatchPattern)
        pat.default_substitute()
        found = conf.font_sort(pat, trim=False, want_coverage=False)[0]
        fnts = [self.fcfound_to_css(f) for f in found]
        fnts = [f for f in fnts if f is not None]
        fnts = sorted(fnts, key=lambda d: d["font-family"])

        ffcs = []
        for f in fnts:
            fm = f["font-family"]
            if selected_families is None or fm in selected_families:
                mysty = "shape-inside:url(#rect1); line-height:1;" + str(f)
                ffcs.append((mysty, f))

        docw = SIZE * 10
        doch = SIZE * 10
        svgstart = '<svg width="{0}mm" height="{1}mm" viewBox="0 0 {0} {1}" id="svg60386" xmlns="http://www.w3.org/2000/svg" xmlns:svg="http://www.w3.org/2000/svg"> <defs id="defs60383" />'
        svgstart += '<rect style="fill:none;stroke:none;" id="rect1" width="{0}" height="{1}" x="0" y="0" />'
        svgstart = svgstart.format(docw, doch)
        svgstop = "</svg>"
        txt1 = '<text xml:space="preserve" style="'
        txt2 = '" id="text'
        txt5 = '">'
        txt6 = "</text>"
        svgtexts = ""
        import tempfile, os

        with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".svg") as f:
            tmpname = os.path.abspath(f.name)
            f.write(svgstart.encode("utf8"))
            from xml.sax.saxutils import escape

            cnt = 0
            for ii in range(len(ffcs)):
                cnt += 1
                c = "I"
                sty = "font-size:" + str(SIZE) + "px; " + ffcs[ii][0]
                svgtexts += txt1 + sty + txt2 + str(cnt) + txt5 + escape(c) + txt6
                if cnt % 1000 == 0:
                    f.write(svgtexts.encode("utf8"))
                    svgtexts = ""

            f.write((svgtexts + svgstop).encode("utf8"))
        from inkex.text.utils import Get_Bounding_Boxes

        bbs = Get_Bounding_Boxes(tmpname)
        # dh.idebug(tmpname)
        # dh.idebug(bbs)

        firsty = dict()
        for ii, ffc in enumerate(ffcs):
            bb = bbs["text" + str(ii + 1)]
            rs = font_style(Style(ffc[1]))
            tf = self.get_true_font(rs)
            firsty[tf] = (bb[1] + bb[3]) / SIZE
        return firsty


# Font style: given a CSS style, return a style that has the four attributes
# that matter for font selection
# Note that Inkscape may not draw this font if it is not present on the system.
# The family can have multiple comma-separated values, used for fallback
fontatt = ["font-family", "font-weight", "font-style", "font-stretch"]
dfltatt = [(k, default_style_atts[k]) for k in fontatt]
from functools import lru_cache


@lru_cache(maxsize=None)
def font_style(sty):
    sty2 = Style(dfltatt)
    sty2.update({k: v for k, v in sty.items() if k in fontatt})
    sty2["font-family"] = ",".join(
        ["'" + v.strip('"').strip("'") + "'" for v in sty2["font-family"].split(",")]
    )
    return sty2


# True style: given a CSS style, return a style with the actual font that
# fontconfig selected.
# This is the actual font that Inkscape will draw
fcfg = FontConfig()


@lru_cache(maxsize=None)
def true_style(sty):
    sty2 = font_style(sty)
    tf = fcfg.get_true_font(sty2)
    return tf


# For dicts whose keys are numerical values, return the value corresponding to
# the closest one
def nearest_val(dictv, width_value):
    return dictv[min(dictv.keys(), key=lambda x: abs(x - width_value))]


# A version of
class FontTools_FontInstance:
    def __init__(self, fcfont):
        self.font = self.font_from_fc(fcfont)

        self.head = self.font["head"]
        self.os2 = self.font["OS/2"] if "OS/2" in self.font else None
        self.find_font_metrics()

    # Find a FontTools font from a found FontConfig font
    def font_from_fc(self, found):
        fname = found.get(fc.PROP.FILE, 0)[0]

        try:
            from fontTools.ttLib import TTFont
        except ModuleNotFoundError:
            current_script_directory = os.path.dirname(os.path.abspath(__file__))
            sys.path += [os.path.join(current_script_directory, "packages")]
            from fontTools.ttLib import TTFont
        import logging

        # logging.getLogger('fontTools.ttLib.tables._h_e_a_d').setLevel(logging.ERROR)
        logging.getLogger("fontTools").setLevel(logging.ERROR)

        try:
            font = TTFont(fname)

            # If font has variants, get them
            if "fvar" in font:
                fcwgt = found.get(fc.PROP.WEIGHT, 0)[0]
                fcsln = found.get(fc.PROP.SLANT, 0)[0]
                fcwdt = found.get(fc.PROP.WIDTH, 0)[0]
                FCWGT_to_OS2WGT = {
                    FC.WEIGHT_THIN: 100,
                    FC.WEIGHT_EXTRALIGHT: 200,
                    FC.WEIGHT_LIGHT: 300,
                    FC.WEIGHT_SEMILIGHT: 350,
                    FC.WEIGHT_BOOK: 380,
                    FC.WEIGHT_NORMAL: 400,
                    FC.WEIGHT_MEDIUM: 500,
                    FC.WEIGHT_SEMIBOLD: 600,
                    FC.WEIGHT_BOLD: 700,
                    FC.WEIGHT_ULTRABOLD: 800,
                    FC.WEIGHT_HEAVY: 900,
                    FC.WEIGHT_ULTRABLACK: 1000,
                }
                location = dict()
                for axis in font["fvar"].axes:
                    if axis.axisTag == "wght":
                        location["wght"] = nearest_val(FCWGT_to_OS2WGT, fcwgt)
                    elif axis.axisTag == "wdth":
                        location["wdth"] = fcwdt
                if len(location) > 0:
                    from fontTools.varLib import mutator

                    font = mutator.instantiateVariableFont(font, location)

        except:
            OS2WDT_to_FCWDT = {
                1: FC.WIDTH_ULTRACONDENSED,
                2: FC.WIDTH_EXTRACONDENSED,
                3: FC.WIDTH_CONDENSED,
                4: FC.WIDTH_SEMICONDENSED,
                5: FC.WIDTH_NORMAL,
                6: FC.WIDTH_SEMIEXPANDED,
                7: FC.WIDTH_EXPANDED,
                8: FC.WIDTH_EXTRAEXPANDED,
                9: FC.WIDTH_ULTRAEXPANDED,
            }
            OS2WGT_to_FCWGT = {
                100: FC.WEIGHT_THIN,
                200: FC.WEIGHT_EXTRALIGHT,
                300: FC.WEIGHT_LIGHT,
                350: FC.WEIGHT_SEMILIGHT,
                380: FC.WEIGHT_BOOK,
                400: FC.WEIGHT_NORMAL,
                500: FC.WEIGHT_MEDIUM,
                600: FC.WEIGHT_SEMIBOLD,
                700: FC.WEIGHT_BOLD,
                800: FC.WEIGHT_ULTRABOLD,
                900: FC.WEIGHT_HEAVY,
                1000: FC.WEIGHT_ULTRABLACK,
            }
            # fcfam = found.get(fc.PROP.FAMILY,0)[0]
            fcwgt = found.get(fc.PROP.WEIGHT, 0)[0]
            fcsln = found.get(fc.PROP.SLANT, 0)[0]
            fcwdt = found.get(fc.PROP.WIDTH, 0)[0]

            from fontTools.ttLib import TTCollection

            collection = TTCollection(fname)
            num_fonts = len(collection)
            collection.close()
            num_match = []
            for i in range(num_fonts):
                tfont = TTFont(fname, fontNumber=i)
                font_weight = tfont["OS/2"].usWeightClass
                font_width = tfont["OS/2"].usWidthClass

                subfamily = tfont["name"].getName(2, 3, 1, 1033)
                subfamily = (
                    subfamily.toUnicode() if subfamily is not None else "Unknown"
                )
                font_italic = (
                    (tfont["OS/2"].fsSelection & 1) != 0
                    or "italic" in subfamily.lower()
                    or "oblique" in subfamily.lower()
                )

                matches = [
                    nearest_val(OS2WGT_to_FCWGT, font_weight) == fcwgt,
                    OS2WDT_to_FCWDT[font_width] == fcwdt,
                    (
                        (font_italic and fcsln in [FC.SLANT_ITALIC, FC.SLANT_OBLIQUE])
                        or (not font_italic and fcsln == FC.SLANT_ROMAN)
                    ),
                ]
                num_match.append(sum(matches))
                if num_match[-1] == 3:
                    # dh.idebug((font_weight,font_width,font_italic))
                    font = tfont
                    break
            if max(num_match) < 3:
                # Did not find a perfect match
                font = [
                    TTFont(fname, fontNumber=i)
                    for i in range(num_fonts)
                    if num_match[i] == max(num_match)
                ][0]
        return font

    # A modified version of Inkscape's find_font_metrics
    # https://gitlab.com/inkscape/inkscape/-/blob/master/src/libnrtype/font-instance.cpp#L267
    # Uses FontTools, which is Pythonic
    def find_font_metrics(self):
        font = self.font
        unitsPerEm = self.head.unitsPerEm
        os2 = self.os2
        if os2:
            self._ascent = abs(os2.sTypoAscender / unitsPerEm)
            self._descent = abs(os2.sTypoDescender / unitsPerEm)
        else:
            self._ascent = abs(font["hhea"].ascent / unitsPerEm)
            self._descent = abs(font["hhea"].descent / unitsPerEm)
        self._ascent_max = abs(font["hhea"].ascent / unitsPerEm)
        self._descent_max = abs(font["hhea"].descent / unitsPerEm)
        self._design_units = unitsPerEm
        em = self._ascent + self._descent
        if em > 0.0:
            self._ascent /= em
            self._descent /= em

        if os2 and os2.version >= 0x0002 and os2.version != 0xFFFF:
            self._xheight = abs(os2.sxHeight / unitsPerEm)
        else:
            glyph_set = font.getGlyphSet()
            self._xheight = (
                abs(glyph_set["x"].height / unitsPerEm)
                if "x" in glyph_set and glyph_set["x"].height is not None
                else 0.5
            )
        self._baselines = [0] * 8
        self.SP_CSS_BASELINE_IDEOGRAPHIC = 0
        self.SP_CSS_BASELINE_HANGING = 1
        self.SP_CSS_BASELINE_MATHEMATICAL = 2
        self.SP_CSS_BASELINE_CENTRAL = 3
        self.SP_CSS_BASELINE_MIDDLE = 4
        self.SP_CSS_BASELINE_TEXT_BEFORE_EDGE = 5
        self.SP_CSS_BASELINE_TEXT_AFTER_EDGE = 6
        self.SP_CSS_BASELINE_ALPHABETIC = 7
        self._baselines[self.SP_CSS_BASELINE_IDEOGRAPHIC] = -self._descent
        self._baselines[self.SP_CSS_BASELINE_HANGING] = 0.8 * self._ascent
        self._baselines[self.SP_CSS_BASELINE_MATHEMATICAL] = 0.8 * self._xheight
        self._baselines[self.SP_CSS_BASELINE_CENTRAL] = 0.5 - self._descent
        self._baselines[self.SP_CSS_BASELINE_MIDDLE] = 0.5 * self._xheight
        self._baselines[self.SP_CSS_BASELINE_TEXT_BEFORE_EDGE] = self._ascent
        self._baselines[self.SP_CSS_BASELINE_TEXT_AFTER_EDGE] = -self._descent

        # Get capital height
        if os2 and hasattr(os2, "sCapHeight") and os2.sCapHeight not in [0, None]:
            self.cap_height = os2.sCapHeight / unitsPerEm
        elif "glyf" in font and "I" in font.getGlyphNames():
            glyf_table = font["glyf"]
            i_glyph = glyf_table["I"]
            self.cap_height = (i_glyph.yMax - 0 * i_glyph.yMin) / unitsPerEm
        else:
            self.cap_height = 1

    def get_char_advances(self, chars, pchars):
        unitsPerEm = self.head.unitsPerEm
        if not hasattr(self, "cmap"):
            self.cmap = self.font.getBestCmap()
        if not hasattr(self, "htmx"):
            self.hmtx = self.font["hmtx"]
        if not hasattr(self, "kern"):
            self.kern = self.font["kern"] if "kern" in self.font else None
        if not hasattr(self, "GSUB"):
            self.gsub = self.font["GSUB"] if "GSUB" in self.font else None
        if not hasattr(self, "glyf"):
            self.glyf = self.font["glyf"] if "glyf" in self.font else None
        if not hasattr(self, "GlyphNames"):
            self.GlyphNames = self.font.getGlyphNames()

        if self.cmap is None:
            # Certain symbol fonts don't have a cmap table
            return None, None, None

        advs = dict()
        bbs = dict()
        for c in chars:
            glyph1 = self.cmap.get(ord(c))
            if glyph1 is not None:
                if glyph1 in self.hmtx.metrics:
                    advance_width, lsb = self.hmtx.metrics[glyph1]
                    advs[c] = advance_width / unitsPerEm

                try:
                    glyph = self.glyf[glyph1]
                    bb = [
                        glyph.xMin,
                        -glyph.yMax,
                        glyph.xMax - glyph.xMin,
                        glyph.yMax - glyph.yMin,
                    ]
                    bbs[c] = [v / unitsPerEm for v in bb]
                except:
                    bbs[c] = [0, 0, 0, 0]
            else:
                advs[c] = None

        # Get ligature table
        if not hasattr(self, "ligatures"):
            if self.gsub:
                gsub_table = self.gsub.table
                # Iterate over each LookupList in the GSUB table
                self.ligatures = dict()
                for lookup_index, lookup in enumerate(gsub_table.LookupList.Lookup):
                    # Each Lookup can contain multiple SubTables
                    for subtable_index, subtable in enumerate(lookup.SubTable):
                        # Handle extension lookups
                        if (
                            lookup.LookupType == 7
                        ):  # 7 is the Lookup type for Extension Substitutions
                            ext_subtable = subtable.ExtSubTable
                            lookup_type = ext_subtable.LookupType
                        else:
                            ext_subtable = subtable
                            lookup_type = lookup.LookupType

                        # We're only interested in ligature substitutions
                        if (
                            lookup_type == 4
                        ):  # 4 is the Lookup type for Ligature Substitutions
                            # Each subtable can define substitutions for multiple glyphs
                            for (
                                first_glyph,
                                ligature_set,
                            ) in ext_subtable.ligatures.items():
                                # The ligature set contains all ligatures that start with the first glyph
                                # Each ligature is a sequence of glyphs that it replaces
                                for ligature in ligature_set:
                                    # The 'Component' field is a list of glyphs that make up the ligature
                                    component_glyphs = [
                                        first_glyph
                                    ] + ligature.Component
                                    # The 'LigGlyph' field is the glyph that the components are replaced with
                                    ligature_glyph = ligature.LigGlyph
                                    self.ligatures[tuple(component_glyphs)] = (
                                        ligature_glyph
                                    )
            else:
                self.ligatures = dict()

        dadvs = dict()
        for c in pchars:
            glyph2 = self.cmap.get(ord(c))
            for pc in pchars[c]:
                glyph1 = self.cmap.get(ord(pc))
                kerning_value = None
                if (glyph1, glyph2) in self.ligatures:
                    ligglyph = self.ligatures[(glyph1, glyph2)]
                    awlig, lsb = self.hmtx.metrics[ligglyph]
                    aw1, lsb = self.hmtx.metrics[glyph1]
                    aw2, lsb = self.hmtx.metrics[glyph2]
                    kerning_value = awlig - aw1 - aw2
                else:
                    if self.kern is not None:
                        for subtable in self.kern.kernTables:
                            kerning_value = subtable.kernTable.get((glyph1, glyph2))
                            if kerning_value is not None:
                                break
                if kerning_value is None:
                    kerning_value = 0
                dadvs[(pc, c)] = kerning_value / unitsPerEm
        return advs, dadvs, bbs


# The Pango library is only available starting with v1.1 (when Inkscape added
# the Python bindings for the gtk library).
with warnings.catch_warnings():
    # Ignore ImportWarning for Gtk/Pango
    warnings.simplefilter("ignore")

    haspango = False
    haspangoFT2 = False
    pangoenv = os.environ.get("USEPANGO", "")
    if not (pangoenv == "False"):
        try:
            import platform

            if platform.system().lower() == "windows":
                # Windows does not have all of the typelibs needed for PangoFT2
                # Manually add the missing ones
                girep = os.path.join(
                    os.path.dirname(os.path.dirname(Get_Binary_Loc())),
                    "lib",
                    "girepository-1.0",
                )
                if os.path.isdir(girep):
                    tlibs = [
                        "fontconfig-2.0.typelib",
                        "PangoFc-1.0.typelib",
                        "PangoFT2-1.0.typelib",
                        "freetype2-2.0.typelib",
                    ]
                    if any([not (os.path.exists(t)) for t in tlibs]):
                        # gi looks in the order specified in GI_TYPELIB_PATH
                        current_script_directory = os.path.dirname(
                            os.path.abspath(__file__)
                        )
                        for newpath in [
                            girep,
                            os.path.join(current_script_directory, "typelibs"),
                        ]:
                            cval = os.environ.get("GI_TYPELIB_PATH", "")
                            if cval == "":
                                os.environ["GI_TYPELIB_PATH"] = newpath
                            elif newpath not in cval:
                                os.environ["GI_TYPELIB_PATH"] = (
                                    cval + os.pathsep + newpath
                                )

            import gi

            gi.require_version("Gtk", "3.0")

            from gi.repository import GLib
            from gi.repository import Pango
            from gi.repository import Gdk

            Pango.Variant.NORMAL
            # make sure this exists
            haspango = True
        except:
            haspango = False

        try:
            # requires some typelibs we do not have
            gi.require_version("PangoFT2", "1.0")
            from gi.repository import PangoFT2

            haspangoFT2 = True
        except:
            haspangoFT2 = False

if pangoenv in ["True", "False"]:
    os.environ["HASPANGO"] = str(haspango)
    os.environ["HASPANGOFT2"] = str(haspangoFT2)
    with open("env_vars.txt", "w") as f:
        f.write(f"HASPANGO={os.environ['HASPANGO']}")
        f.write(f"\nHASPANGOFT2={os.environ['HASPANGOFT2']}")

# inkex.utils.debug(haspango)
# inkex.utils.debug(haspangoFT2)

global numerrlogs
numerrlogs = 0


class PangoRenderer:
    def __init__(self):
        self.PANGOSIZE = 1024 * 4
        # size of text to render. 1024 is good

        # warnings.simplefilter('ignore')
        # GTk warning suppression from Martin Owens
        # Can sometimes suppress debug output also?
        def _nope(*args, **kwargs):  #
            global numerrlogs
            numerrlogs += 1
            return GLib.LogWriterOutput.HANDLED

        # GLib.log_set_writer_func(_nope, None)
        # Disabled 2023.09.26 because was causing crashing after refactoring
        # I believe this is fine since haspangoFT2 always true now

        if haspangoFT2:
            # dh.idebug('PangoFT2')
            self.ctx = Pango.Context.new()
            self.ctx.set_font_map(PangoFT2.FontMap.new())
        else:
            self.ctx = Gdk.pango_context_get()
        self.pangolayout = Pango.Layout(self.ctx)

        def css_to_pango_func(sty, key):
            val = sty.get(key)
            if key == "font-weight":
                return lu.CSSWGT_to_PWGT.get(val, Pango.Weight.NORMAL)
            elif key == "font-style":
                return lu.CSSSTY_to_PSTY.get(val, Pango.Style.NORMAL)
            elif key == "font-stretch":
                return lu.CSSSTR_to_PSTR.get(val, Pango.Stretch.NORMAL)
            elif key == "font-variant":
                return lu.CSSVAR_to_PVAR.get(val, Pango.Variant.NORMAL)
            return None

        # self.css_to_pango = css_to_pango_func;

        def css_to_pango_description_fcn(sty):
            from gi.repository import Pango

            fd = Pango.FontDescription(sty["font-family"].strip("'") + ",")
            # The comma above is very important for font-families like Rockwell Condensed.
            # Without it, Pango will interpret it as the Condensed font-stretch of the Rockwell font-family,
            # rather than the Rockwell Condensed font-family.
            fd.set_weight(
                lu.CSSWGT_to_PWGT.get(sty.get("font-weight"), Pango.Weight.NORMAL)
            )
            fd.set_variant(
                lu.CSSVAR_to_PVAR.get(sty.get("font-variant"), Pango.Variant.NORMAL)
            )
            fd.set_style(
                lu.CSSSTY_to_PSTY.get(sty.get("font-style"), Pango.Style.NORMAL)
            )
            fd.set_stretch(
                lu.CSSSTR_to_PSTR.get(sty.get("font-stretch"), Pango.Stretch.NORMAL)
            )
            return fd

        self.css_to_pango_description = css_to_pango_description_fcn

        def pango_to_fc_func(pstretch, pweight, pstyle):
            fcwidth = lu.PSTR_to_FCWDT[pstretch]
            fcweight = lu.PWGT_to_FCWGT[pweight]
            fcslant = lu.PSTY_to_FCSLN[pstyle]
            return fcwidth, fcweight, fcslant

        self.pango_to_fc = pango_to_fc_func

        def pango_to_css_func(pdescription):
            fd = pdescription

            def isnumeric(s):
                try:
                    float(s)
                    isnum = True
                except:
                    isnum = False
                return isnum

            cs = [k for k, v in lu.CSSSTR_to_PSTR.items() if v == fd.get_stretch()]
            cw = [
                k
                for k, v in lu.CSSWGT_to_PWGT.items()
                if v == fd.get_weight() and isnumeric(k)
            ]
            csty = [k for k, v in lu.CSSSTY_to_PSTY.items() if v == fd.get_style()]

            s = (("font-family", fd.get_family()),)
            if len(cs) > 0:
                s += (("font-stretch", cs[0]),)
            if len(cw) > 0:
                s += (("font-weight", cw[0]),)
            if len(csty) > 0:
                s += (("font-style", csty[0]),)
            return Style(s)

        self.pango_to_css = pango_to_css_func

        self.pufd = Pango.units_from_double
        self.putd = Pango.units_to_double
        self.scale = Pango.SCALE

    @property
    def families(self):
        if not hasattr(self, "_families"):
            families = self.ctx.get_font_map().list_families()
            self._families = sorted(
                families, key=lambda x: x.get_name()
            )  # Sort families alphabetically
        return self._families

    @property
    def faces(self):
        if not hasattr(self, "_faces"):
            self._faces = [fc for fm in self.families for fc in fm.list_faces()]
        return self._faces

    @property
    def face_descriptions(self):
        if not hasattr(self, "_face_descriptions"):
            self._face_descriptions = [fc.describe() for fc in self.faces]
        return self._face_descriptions

    @property
    def face_strings(self):
        if not hasattr(self, "_face_strings"):
            self._face_strings = [fd.to_string() for fd in self.face_descriptions]
        return self._face_strings

    @property
    def face_css(self):
        if not hasattr(self, "_face_css"):
            self._face_css = [self.pango_to_css(fd) for fd in self.face_descriptions]
        return self._face_css

    # Search the /etc/fonts/conf.d folder for the default sans-serif font
    # Not currently used
    def Find_Default_Sanserifs(self):
        bloc = Get_Binary_Loc()

        import platform

        ikdir = os.path.dirname(os.path.dirname(os.path.abspath(bloc)))
        if platform.system().lower() == "darwin":
            confd = os.path.join(
                os.path.join(ikdir, "Resources", "etc", "fonts", "conf.d")
            )
        elif platform.system().lower() == "windows":
            confd = os.path.join(os.path.join(ikdir, "etc", "fonts", "conf.d"))
        else:
            confd = os.path.join(os.path.join(ikdir, "etc", "fonts", "conf.d"))

        fns = [
            f.name
            for f in os.scandir(confd)
            if len(f.name) > 1 and f.name[0:2] in [str(v) for v in range(60, 70)]
        ]
        fns = [os.path.join(confd, f) for f in fns]

        gfn = [fn for fn in fns if os.path.split(fn)[1] == "60-latin.conf"]
        if len(gfn) > 0:
            fns = gfn  # use 60-latin.conf if it is available, otherwise find the first

        ssbackups = []
        for fn in fns:
            from lxml import etree

            mytree = etree.parse(fn)
            myroot = mytree.getroot()

            for el in myroot.getchildren():
                if el.tag == "alias":
                    for el2 in el.getchildren():
                        if el2.tag == "family" and el2.text == "sans-serif":
                            nextel = next(el2.itersiblings())
                            if nextel.tag == "prefer":
                                for el3 in nextel.getchildren():
                                    if el3.tag == "family":
                                        ssbackups.append(el3.text)
            if len(ssbackups) > 0:
                break
        return ssbackups

    # Look up a font by its Pango properties
    def fc_match_pango(self, family, pstretch, pweight, pstyle):
        pat = fc.Pattern.name_parse(re.escape(family.replace("'", "").replace('"', "")))
        fcwidth, fcweight, fcslant = self.pango_to_fc(pstretch, pweight, pstyle)
        pat.add(fc.PROP.WIDTH, fcwidth)
        pat.add(fc.PROP.WEIGHT, fcweight)
        pat.add(fc.PROP.SLANT, fcslant)

        conf = fc.Config.get_current()
        conf.substitute(pat, FC.MatchPattern)
        pat.default_substitute()
        found, status = conf.font_match(pat)
        # fcname = found.get(fc.PROP.FULLNAME,0)[0];
        # fcfm   = found.get(fc.PROP.FAMILY,0)[0];
        return found

    def Set_Text_Style(self, stystr):
        sty2 = stystr.split(";")
        sty2 = {s.split(":")[0]: s.split(":")[1] for s in sty2}

        msty = [
            "font-family",
            "font-weight",
            "font-style",
            "font-variant",
            "font-stretch",
        ]  # mandatory style
        for m in msty:
            if m not in sty2:
                sty2[m] = default_style_atts[m]

        fd = self.css_to_pango_description(sty2)
        fd.set_absolute_size(self.pufd(self.PANGOSIZE))
        global numerrlogs
        logsbefore = numerrlogs
        fnt = self.ctx.get_font_map().load_font(self.ctx, fd)

        if not (haspangoFT2):
            success = numerrlogs == logsbefore and fnt is not None
        else:
            success = fnt is not None
            # PangoFT2 sometimes gives mysterious errors that are actually fine

        if success:
            self.pangolayout.set_font_description(fd)
            fm = fnt.get_metrics()
            fm = [
                self.putd(v) / self.PANGOSIZE
                for v in [fm.get_height(), fm.get_ascent(), fm.get_descent()]
            ]
            return success, fm
        else:
            return success, None

    def Render_Text(self, texttorender):
        self.pangolayout.set_text(texttorender, -1)

    # Scale extents and return extents as standard bboxes
    # (0:logical, 1:ink, 2: ink relative to anchor/baseline)
    def process_extents(self, ext, ascent):
        lr = ext.logical_rect
        lr = [self.putd(v) / self.PANGOSIZE for v in [lr.x, lr.y, lr.width, lr.height]]
        ir = ext.ink_rect
        ir = [self.putd(v) / self.PANGOSIZE for v in [ir.x, ir.y, ir.width, ir.height]]
        ir_rel = [ir[0] - lr[0], ir[1] - lr[1] - ascent, ir[2], ir[3]]
        return lr, ir, ir_rel

    def Get_Character_Extents(self, ascent, needexts):
        # Iterate through the layout to get the logical width of each character
        # If there is differential kerning applied, it is applied to the
        # width of the first character. For example, the 'V' in 'Voltage'
        # will be thinner due to the 'o' that follows.
        # Units: relative to font size
        loi = self.pangolayout.get_iter()
        ws = []
        ii = -1
        lastpos = True
        unwrapper = 0
        moved = True
        while moved:
            ce = loi.get_cluster_extents()
            ii += 1
            if needexts[ii] == "1":
                ext = self.process_extents(ce, ascent)
                if ext[0][0] < 0 and lastpos:
                    unwrapper += 2**32 / (self.scale * self.PANGOSIZE)
                lastpos = ext[0][0] >= 0
                ext[0][0] += unwrapper  # account for 32-bit overflow
                ext[1][0] += unwrapper
                ws.append(ext)
            else:
                ws.append(None)
            moved = loi.next_char()

        numunknown = self.pangolayout.get_unknown_glyphs_count()
        return ws, numunknown

    # For testing purposes
    def Font_Test_Doc(self):
        selected_families = [
            "Arial",
            "Rockwell",
            "Rockwell Condensed",
            "Rockwell Extra-Bold",
            "Bahnschrift",
            "Avenir",
            "Avenir Next",
            "Tahoma",
            "Cambria Math",
            "Whitney",
            "Helvetica",
            "Whitney Book",
            "Modern No. 20",
        ]

        # selected_families = None
        def isnumeric(s):
            try:
                float(s)
                isnum = True
            except:
                isnum = False
            return isnum

        ffcs = []
        ffcs = [("font-family: InvalidFont", "InvalidFont")]
        for fd in self.all_desc:
            fm = fd.get_family()
            if selected_families is None or fm in selected_families:
                fs = fd.get_stretch()
                fw = fd.get_weight()
                fsty = fd.get_style()

                cs = [k for k, v in lu.CSSSTR_to_PSTR.items() if v == fs]
                cw = [
                    k for k, v in lu.CSSWGT_to_PWGT.items() if v == fw and isnumeric(k)
                ]
                csty = [k for k, v in lu.CSSSTY_to_PSTY.items() if v == fsty]

                mysty = "font-family:'" + fm + "'; "
                if len(cs) > 0:
                    mysty += "font-stretch: " + cs[0] + "; "
                if len(cw) > 0:
                    mysty += "font-weight: " + cw[0] + "; "
                if len(csty) > 0:
                    mysty += "font-style: " + csty[0] + "; "
                ffcs.append((mysty, fd.to_string()))

        rng = range(0, len(ffcs))
        HGT = 45
        WDH = (max(rng) - (max(rng) % HGT)) / HGT + 1
        SIZE = 1
        LINEW = 25
        doch = (HGT + 1) * SIZE
        docw = WDH * SIZE * LINEW

        svgstart = (
            '<svg width="'
            + str(docw)
            + 'mm" height="'
            + str(doch)
            + 'mm" viewBox="0 0 '
            + str(docw)
            + " "
            + str(doch)
            + '" id="svg60386" xmlns="http://www.w3.org/2000/svg" xmlns:svg="http://www.w3.org/2000/svg"> <defs id="defs60383" />'
        )
        svgstop = "</svg>"
        txt1 = '<text xml:space="preserve" style="'
        txt2 = '" id="text'
        txt3 = '" y="'
        txt4 = '" x="'
        txt5 = '">'
        txt6 = "</text>"
        svgtexts = ""
        import tempfile, os

        f = tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".svg")
        tmpname = os.path.abspath(f.name)
        f.write(svgstart.encode("utf8"))
        from xml.sax.saxutils import escape

        cnt = 0
        for ii in range(len(ffcs)):
            cnt += 1
            c = ffcs[ii][1]
            sty = "font-size:" + str(SIZE) + "px; " + ffcs[ii][0]
            x = str(LINEW * (ii - (ii % HGT)) / HGT * SIZE) + "px"
            y = str((ii % HGT + 1) * SIZE) + "px"
            svgtexts += (
                txt1
                + sty
                + txt2
                + str(cnt)
                + txt3
                + y
                + txt4
                + x
                + txt5
                + escape(c)
                + txt6
            )
            if cnt % 1000 == 0:
                f.write(svgtexts.encode("utf8"))
                svgtexts = ""

        f.write((svgtexts + svgstop).encode("utf8"))
        f.close()
        inkex.utils.debug(tmpname)


# For testing purposes
def Unicode_Test_Doc():
    rng = range(1, 10000)
    HGT = 256
    WDH = (max(rng) - (max(rng) % HGT)) / HGT + 1
    SIZE = 1
    doch = (HGT + 1) * SIZE
    docw = WDH * SIZE

    svgstart = (
        '<svg width="'
        + str(docw)
        + 'mm" height="'
        + str(doch)
        + 'mm" viewBox="0 0 '
        + str(docw)
        + " "
        + str(doch)
        + '" id="svg60386" xmlns="http://www.w3.org/2000/svg" xmlns:svg="http://www.w3.org/2000/svg"> <defs id="defs60383" />'
    )
    svgstop = "</svg>"
    txt1 = '<text xml:space="preserve" style="'
    txt2 = '" id="text'
    txt3 = '" y="'
    txt4 = '" x="'
    txt5 = '">'
    txt6 = "</text>"
    svgtexts = ""
    import tempfile, os

    f = tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".svg")
    tmpname = os.path.abspath(f.name)
    f.write(svgstart.encode("utf8"))
    from xml.sax.saxutils import escape

    cnt = 0
    for ii in rng:
        cnt += 1
        c = chr(ii)
        sty = "font-size:" + str(SIZE) + "px"
        x = str((ii - (ii % HGT)) / HGT * SIZE) + "px"
        y = str((ii % HGT + 1) * SIZE) + "px"
        svgtexts += (
            txt1 + sty + txt2 + str(cnt) + txt3 + y + txt4 + x + txt5 + escape(c) + txt6
        )
        if cnt % 1000 == 0:
            f.write(svgtexts.encode("utf8"))
            svgtexts = ""

    f.write((svgtexts + svgstop).encode("utf8"))
    f.close()

    def overwrite_output(filein, fileout):
        try:
            os.remove(fileout)
        except:
            pass
        arg2 = [
            Get_Binary_Loc(),
            "--export-background",
            "#ffffff",
            "--export-background-opacity",
            "1.0",
            "--export-filename",
            fileout,
            filein,
        ]
        from inkex.text.utils import subprocess_repeat

        subprocess_repeat(arg2)

    tmp2 = tmpname.replace(".svg", ".pdf")
    tmp3 = tmpname.replace(".svg", "_2.svg")

    overwrite_output(tmpname, tmp2)
    overwrite_output(tmp2, tmp3)
    svg2 = inkex.load_svg(tmp3).getroot()


def Pango_Test():
    # For testing Gtk-based Pango rendering, modified from
    # https://web.archive.org/web/20180615145907/http://jcoppens.com/soft/howto/pygtk/pangocairo.en.php
    # Uses PangoCairo to output to a png
    # Only works in Inkscape v1.1
    import gi

    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Pango

    from gi.repository import PangoCairo as pc
    from gi.repository import cairo

    inkex.utils.debug(dir(cairo))

    RADIUS = 500
    FONT = "Bahnschrift Light Condensed, " + str(RADIUS / 5)
    filename = "Pango test.png"

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 2 * RADIUS, int(RADIUS / 2))
    cairo_ctx = cairo.Context(surface)

    cairo_ctx.set_source_rgb(1.0, 1.0, 1.0)
    cairo_ctx.paint()

    cairo_ctx.translate(RADIUS, 0)
    pc_ctx = pc.create_context(cairo_ctx)
    pc_layout = pc.create_layout(cairo_ctx)

    desc = Pango.FontDescription(FONT)
    # desc.set_stretch(Pango.Stretch.CONDENSED)
    # desc.set_weight(Pango.Weight.LIGHT)

    markup = 'Test 123 <span font-family="Cambria Math">⎣</span>'
    pm = Pango.parse_markup(markup, -1, "\x00")
    pc_layout.set_attributes(pm[1])
    pc_layout.set_text(pm[2])

    pc_layout.set_font_description(desc)

    cairo_ctx.save()
    cairo_ctx.set_source_rgb(0, 0, 0)
    pc.update_layout(cairo_ctx, pc_layout)

    width, height = pc_layout.get_size()
    cairo_ctx.move_to(-(float(width) / Pango.SCALE) / 2, 0)
    pc.show_layout(cairo_ctx, pc_layout)

    cairo_ctx.restore()
    success = surface.write_to_png(filename)

    fnt = pc_ctx.get_font_map().load_font(pc_ctx, desc)
    fntset = pc_ctx.get_font_map().load_fontset(
        pc_ctx, desc, Pango.Language.get_default()
    )
    # dh.idebug(fntset.get_font(ord('⎣')).describe().to_string())

    from gi.repository import Gdk

    fm2 = Gdk.pango_context_get().get_font_map().list_families()
    all_faces = [fc.describe().to_string() for fm in fm2 for fc in fm.list_faces()]

    families = pc_ctx.get_font_map().list_families()
    fmdict = {
        f.get_name(): [fc.get_face_name() for fc in f.list_faces()] for f in families
    }


# Directly probes libpango to find when line breaks are allowed in text
# Should work in all versions, even ones without gtk
# Not currently used by anything, but a good reference...
# Example: pango_line_breaks('test asdf-measurement')
def pango_line_breaks(txt):
    import sys

    LIBNAME = {
        "linux": "libpango-1.0.so.0",
        "openbsd6": "libpango-1.0.so.0",
        "darwin": "libpango-1.0.dylib",
        "win32": "libpango-1.0-0.dll",
    }[sys.platform]
    import ctypes as ct

    try:
        pango = ct.CDLL(LIBNAME)
    except FileNotFoundError:
        blocdir = os.path.dirname(Get_Binary_Loc())
        fpath = os.path.abspath(os.path.join(blocdir, LIBNAME))
        pango = ct.CDLL(fpath)  # Update this as per your system

    # We can't directly access the PangoLogAttr struct from Python,
    # but we can create a similar struct using ctypes.
    class PangoLogAttr(ct.Structure):
        _fields_ = [("value", ct.c_uint32)]  # Treat the whole struct as a single uint32

    def unpack_log_attr(log_attr):
        return {
            "is_line_break": (log_attr.value >> 0) & 1,
            "is_mandatory_break": (log_attr.value >> 1) & 1,
            "is_char_break": (log_attr.value >> 2) & 1,
            "is_white": (log_attr.value >> 3) & 1,
            "is_cursor_position": (log_attr.value >> 4) & 1,
            "is_word_start": (log_attr.value >> 5) & 1,
            "is_word_end": (log_attr.value >> 6) & 1,
            "is_sentence_boundary": (log_attr.value >> 7) & 1,
            "is_sentence_start": (log_attr.value >> 8) & 1,
            "is_sentence_end": (log_attr.value >> 9) & 1,
            "backspace_deletes_character": (log_attr.value >> 10) & 1,
            "is_expandable_space": (log_attr.value >> 11) & 1,
            "is_word_boundary": (log_attr.value >> 12) & 1,
            "break_inserts_hyphen": (log_attr.value >> 13) & 1,
            "break_removes_preceding": (log_attr.value >> 14) & 1,
            "reserved": (log_attr.value >> 15) & ((1 << 17) - 1),
        }

    # Get default language - again, note this is a simplification
    pango.pango_language_get_default.restype = ct.c_void_p
    default_language = pango.pango_language_get_default()

    # Get log attrs function
    pango.pango_get_log_attrs.restype = None
    pango.pango_get_log_attrs.argtypes = [
        ct.c_char_p,
        ct.c_int,
        ct.c_int,
        ct.c_void_p,
        ct.POINTER(PangoLogAttr),
        ct.c_int,
    ]

    txt = txt.encode("utf-8")
    attrs = (PangoLogAttr * (len(txt) + 1))()
    pango.pango_get_log_attrs(txt, len(txt), -1, default_language, attrs, len(attrs))

    line_breaks = [bool(unpack_log_attr(attr)["is_line_break"]) for attr in attrs]
    # dh.idebug([c for ii,c in enumerate(txt) if line_breaks[ii+1]]x)

    for ii, c in enumerate(txt):
        inkex.utils.debug((chr(txt[ii]), line_breaks[ii]))


class FontAttributeLookups:
    # Conversions between CSS, FontConfig, and Pango font attributes
    # CSS:        font-weight, font-style, font-stretch
    # FontConfig: weight,      slant,      width
    # Pango:      weight,      style,      stretch
    #
    # Inkscape conventions in libnrtype/font-factory.cpp
    # https://gitlab.com/inkscape/inkscape/-/blob/master/src/libnrtype/font-factory.cpp

    def __init__(self):
        # CSS to fontconfig
        # For weights, Inkscape ignores anything commented out below
        # See ink_font_description_from_style in libnrtype/font-factory.cpp
        self.CSSWGT_to_FCWGT = {
            # 'thin'      : FC.WEIGHT_THIN,
            # 'ultralight': FC.WEIGHT_EXTRALIGHT,
            # 'light'     : FC.WEIGHT_LIGHT,
            # 'semilight' : FC.WEIGHT_SEMILIGHT,
            # 'book'      : FC.WEIGHT_BOOK,
            "normal": FC.WEIGHT_NORMAL,
            # 'medium'    : FC.WEIGHT_MEDIUM,
            # 'semibold'  : FC.WEIGHT_SEMIBOLD,
            "bold": FC.WEIGHT_BOLD,
            # 'ultrabold' : FC.WEIGHT_ULTRABOLD,
            # 'heavy'     : FC.WEIGHT_HEAVY,
            # 'ultraheavy': FC.WEIGHT_ULTRABLACK,
            "100": FC.WEIGHT_THIN,
            "200": FC.WEIGHT_EXTRALIGHT,
            "300": FC.WEIGHT_LIGHT,
            # '350'       : FC.WEIGHT_SEMILIGHT,
            # '380'       : FC.WEIGHT_BOOK,
            "400": FC.WEIGHT_NORMAL,
            "500": FC.WEIGHT_MEDIUM,
            "600": FC.WEIGHT_SEMIBOLD,
            "700": FC.WEIGHT_BOLD,
            "800": FC.WEIGHT_ULTRABOLD,
            "900": FC.WEIGHT_HEAVY,
            # '1000'      : FC.WEIGHT_ULTRABLACK
        }

        self.CSSSTY_to_FCSLN = {
            "normal": FC.SLANT_ROMAN,
            "italic": FC.SLANT_ITALIC,
            "oblique": FC.SLANT_OBLIQUE,
        }

        self.CSSSTR_to_FCWDT = {
            "ultra-condensed": FC.WIDTH_ULTRACONDENSED,
            "extra-condensed": FC.WIDTH_EXTRACONDENSED,
            "condensed": FC.WIDTH_CONDENSED,
            "semi-condensed": FC.WIDTH_SEMICONDENSED,
            "normal": FC.WIDTH_NORMAL,
            "semi-expanded": FC.WIDTH_SEMIEXPANDED,
            "expanded": FC.WIDTH_EXPANDED,
            "extra-expanded": FC.WIDTH_EXTRAEXPANDED,
            "ultra-expanded": FC.WIDTH_ULTRAEXPANDED,
        }

        # Fontconfig to CSS
        # Semi-Light, Book, and Ultra-Black are mapped to Light, Normal, Heavy
        # See FontFactory::GetUIStyles in libnrtype/font-factory.cpp
        self.FCWGT_to_CSSWGT = {
            FC.WEIGHT_THIN: "100",
            FC.WEIGHT_EXTRALIGHT: "200",
            FC.WEIGHT_LIGHT: "300",
            # FC.WEIGHT_SEMILIGHT  : '350',
            FC.WEIGHT_SEMILIGHT: "300",
            # FC.WEIGHT_BOOK       : '380',
            FC.WEIGHT_BOOK: "400",
            FC.WEIGHT_NORMAL: "400",
            FC.WEIGHT_MEDIUM: "500",
            FC.WEIGHT_SEMIBOLD: "600",
            FC.WEIGHT_BOLD: "700",
            FC.WEIGHT_ULTRABOLD: "800",
            FC.WEIGHT_HEAVY: "900",
            # FC.WEIGHT_ULTRABLACK : '1000',
            FC.WEIGHT_ULTRABLACK: "900",
        }

        self.FCSLN_to_CSSSTY = {
            FC.SLANT_ROMAN: "normal",
            FC.SLANT_ITALIC: "italic",
            FC.SLANT_OBLIQUE: "oblique",
        }

        self.FCWDT_to_CSSSTR = {
            FC.WIDTH_ULTRACONDENSED: "ultra-condensed",
            FC.WIDTH_EXTRACONDENSED: "extra-condensed",
            FC.WIDTH_CONDENSED: "condensed",
            FC.WIDTH_SEMICONDENSED: "semi-condensed",
            FC.WIDTH_NORMAL: "normal",
            FC.WIDTH_SEMIEXPANDED: "semi-expanded",
            FC.WIDTH_EXPANDED: "expanded",
            FC.WIDTH_EXTRAEXPANDED: "extra-expanded",
            FC.WIDTH_ULTRAEXPANDED: "ultra-expanded",
        }

        if haspango:
            # Pango to fontconfig
            self.PWGT_to_FCWGT = {
                Pango.Weight.THIN: FC.WEIGHT_THIN,
                Pango.Weight.ULTRALIGHT: FC.WEIGHT_ULTRALIGHT,
                Pango.Weight.ULTRALIGHT: FC.WEIGHT_EXTRALIGHT,
                Pango.Weight.LIGHT: FC.WEIGHT_LIGHT,
                Pango.Weight.SEMILIGHT: FC.WEIGHT_DEMILIGHT,
                Pango.Weight.SEMILIGHT: FC.WEIGHT_SEMILIGHT,
                Pango.Weight.BOOK: FC.WEIGHT_BOOK,
                Pango.Weight.NORMAL: FC.WEIGHT_REGULAR,
                Pango.Weight.NORMAL: FC.WEIGHT_NORMAL,
                Pango.Weight.MEDIUM: FC.WEIGHT_MEDIUM,
                Pango.Weight.SEMIBOLD: FC.WEIGHT_DEMIBOLD,
                Pango.Weight.SEMIBOLD: FC.WEIGHT_SEMIBOLD,
                Pango.Weight.BOLD: FC.WEIGHT_BOLD,
                Pango.Weight.ULTRABOLD: FC.WEIGHT_EXTRABOLD,
                Pango.Weight.ULTRABOLD: FC.WEIGHT_ULTRABOLD,
                Pango.Weight.HEAVY: FC.WEIGHT_BLACK,
                Pango.Weight.HEAVY: FC.WEIGHT_HEAVY,
                Pango.Weight.ULTRAHEAVY: FC.WEIGHT_EXTRABLACK,
                Pango.Weight.ULTRAHEAVY: FC.WEIGHT_ULTRABLACK,
            }

            self.PSTY_to_FCSLN = {
                Pango.Style.NORMAL: FC.SLANT_ROMAN,
                Pango.Style.ITALIC: FC.SLANT_ITALIC,
                Pango.Style.OBLIQUE: FC.SLANT_OBLIQUE,
            }

            self.PSTR_to_FCWDT = {
                Pango.Stretch.ULTRA_CONDENSED: FC.WIDTH_ULTRACONDENSED,
                Pango.Stretch.EXTRA_CONDENSED: FC.WIDTH_EXTRACONDENSED,
                Pango.Stretch.CONDENSED: FC.WIDTH_CONDENSED,
                Pango.Stretch.SEMI_CONDENSED: FC.WIDTH_SEMICONDENSED,
                Pango.Stretch.NORMAL: FC.WIDTH_NORMAL,
                Pango.Stretch.SEMI_EXPANDED: FC.WIDTH_SEMIEXPANDED,
                Pango.Stretch.EXPANDED: FC.WIDTH_EXPANDED,
                Pango.Stretch.EXTRA_EXPANDED: FC.WIDTH_EXTRAEXPANDED,
                Pango.Stretch.ULTRA_EXPANDED: FC.WIDTH_ULTRAEXPANDED,
            }
            # CSS to Pango
            self.CSSVAR_to_PVAR = {
                "normal": Pango.Variant.NORMAL,
                "small-caps": Pango.Variant.SMALL_CAPS,
            }

            self.CSSSTY_to_PSTY = {
                "normal": Pango.Style.NORMAL,
                "italic": Pango.Style.ITALIC,
                "oblique": Pango.Style.OBLIQUE,
            }
            # For weights, Inkscape ignores anything commented out below
            # See ink_font_description_from_style in libnrtype/font-factory.cpp
            self.CSSWGT_to_PWGT = {
                # 'thin'       : Pango.Weight.THIN,
                # 'ultralight' : Pango.Weight.ULTRALIGHT,
                # 'light'      : Pango.Weight.LIGHT,
                # 'semilight'  : Pango.Weight.SEMILIGHT,
                # 'book'       : Pango.Weight.BOOK,
                "normal": Pango.Weight.NORMAL,
                # 'medium'     : Pango.Weight.MEDIUM,
                # 'semibold'   : Pango.Weight.SEMIBOLD,
                "bold": Pango.Weight.BOLD,
                # 'ultrabold'  : Pango.Weight.ULTRABOLD,
                # 'heavy'      : Pango.Weight.HEAVY,
                # 'ultraheavy' : Pango.Weight.ULTRAHEAVY,
                "100": Pango.Weight.THIN,
                "200": Pango.Weight.ULTRALIGHT,
                "300": Pango.Weight.LIGHT,
                # '350'        : Pango.Weight.SEMILIGHT,
                # '380'        : Pango.Weight.BOOK,
                "400": Pango.Weight.NORMAL,
                "500": Pango.Weight.MEDIUM,
                "600": Pango.Weight.SEMIBOLD,
                "700": Pango.Weight.BOLD,
                "800": Pango.Weight.ULTRABOLD,
                "900": Pango.Weight.HEAVY,
                # '1000'       : Pango.Weight.ULTRAHEAVY
            }
            self.CSSSTR_to_PSTR = {
                "ultra-condensed": Pango.Stretch.ULTRA_CONDENSED,
                "extra-condensed": Pango.Stretch.EXTRA_CONDENSED,
                "condensed": Pango.Stretch.CONDENSED,
                "semi-condensed": Pango.Stretch.SEMI_CONDENSED,
                "normal": Pango.Stretch.NORMAL,
                "semi-expanded": Pango.Stretch.SEMI_EXPANDED,
                "expanded": Pango.Stretch.EXPANDED,
                "extra-expanded": Pango.Stretch.EXTRA_EXPANDED,
                "ultra-expanded": Pango.Stretch.ULTRA_EXPANDED,
            }


lu = FontAttributeLookups()
